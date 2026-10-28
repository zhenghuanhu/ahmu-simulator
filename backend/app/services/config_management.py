"""
构型管理服务 (技术方案 4.3.13 构型管理功能)
- 周期性接收来自成员系统的构型报告 (成员系统通过"设备标识状态消息"报告设备构型数据)
- 对构型报告内容进行校验比对:
    1) 与飞机基本构型报告比对 -> 不一致生成一条构型错误报告
    2) 与上一周期该成员系统构型报告比对 -> 不一致则更新数据库中的构型报告
- 维护人员可从人机界面请求某个成员系统的构型报告并展示构型信息
- 支持不少于 400 个成员系统的构型报告
"""
import asyncio
import json
import random
import socket
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, BaseConfig, MemberConfig, ConfigErrorReport, AcrConfigReport
from app.core.websocket_manager import ws_manager
from app.core import icd_config_acr as acr_icd
from app.services.lifecycle import MEMBER_SYSTEMS, MEMBER_SYSTEM_MAP
from app.config import CONFIG_REPORT_RX_CONFIG

# 设备标识状态消息包含的构型项 (item, 类型, 中文名)
CONFIG_ITEMS = [
    ("MF", "hardware", "制造商名称"),
    ("MFR", "hardware", "制造商代码"),
    ("PNR", "hardware", "产品件号"),
    ("SER", "hardware", "产品序列号"),
    ("DMF", "hardware", "生产日期"),
    ("SW_PN", "software", "软件件号"),
    ("SW_LOC", "software", "软件位置"),
]
CONFIG_ITEM_NAMES = {code: name for code, _, name in CONFIG_ITEMS}
CONFIG_ITEM_TYPES = {code: t for code, t, _ in CONFIG_ITEMS}
CONFIG_ITEM_CODES = [c for c, _, _ in CONFIG_ITEMS]

# 成员系统总数 (真实48 + 虚拟补足, 满足"不少于400个成员系统")
CONFIG_MEMBER_COUNT = 400


class ConfigManagementService:
    """构型管理服务"""

    def __init__(self):
        self._running = False
        self._base_config: dict[str, dict[str, str]] = {}   # member -> {item: base_value}
        self._current_config: dict[str, dict[str, str]] = {}  # member -> {item: current_value} (持久演化)
        self._member_catalog: list[dict] = []               # [{code, name, ata}]
        self.CHANGE_RATE = 0.03                             # 每构型项构型变更概率 (模拟构型偏离)
        self.RECEIVE_INTERVAL = 5.0                         # 周期接收间隔(秒)
        self.RECEIVE_BATCH = 15                             # 每周期处理的成员系统数
        # ACR (飞机构型报告) 目录: equipID 数字标识 <-> 成员系统 映射
        self._acr_catalog: list[dict] = []                  # [{equipID, ata, equipName, memberCode}]
        self._acr_by_equipid: dict[int, dict] = {}
        self._acr_by_ata: dict[str, list[dict]] = {}
        # 构型报告 UDP 接收 (Mock AFDX)
        self._rx_sock: Optional[socket.socket] = None
        self._rx_host = CONFIG_REPORT_RX_CONFIG.get("recv_host", "0.0.0.0")
        self._rx_port = CONFIG_REPORT_RX_CONFIG.get("recv_port", 34840)
        # ACR 构型请求 UDP 接收 (Deal_Type 1=429/2=664)
        self._acr_req_sock: Optional[socket.socket] = None
        self._acr_req_port = CONFIG_REPORT_RX_CONFIG.get("acr_request_port", 34841)

    # ==================== 生命周期 ====================

    async def start(self):
        """启动构型管理服务"""
        self._running = True
        self._build_member_catalog()
        self._init_base_config()
        self._build_acr_catalog()
        self._setup_rx_socket()
        self._setup_acr_req_socket()
        logger.info("构型管理服务已启动")
        asyncio.create_task(self._config_receive_loop())
        asyncio.create_task(self._config_report_rx_loop())
        asyncio.create_task(self._acr_request_rx_loop())

    async def stop(self):
        self._running = False
        self._close_rx_socket()
        self._close_acr_req_socket()
        logger.info("构型管理服务已停止")

    def _build_member_catalog(self):
        """构建成员系统名录 (真实48 + 虚拟补足到400)"""
        catalog = [{"code": code, "name": name, "ata": ata}
                   for code, name, ata in MEMBER_SYSTEMS]
        for i in range(1, CONFIG_MEMBER_COUNT - len(catalog) + 1):
            code = f"MEM{i:03d}"
            catalog.append({"code": code, "name": f"虚拟成员系统-{i:03d}",
                            "ata": f"{(i % 60) + 1:02d}"})
        self._member_catalog = catalog

    def _init_base_config(self):
        """初始化飞机基本构型报告 (400个成员系统, 落库 BaseConfig)"""
        db = SessionLocal()
        try:
            for m in self._member_catalog:
                member = m["code"]
                base_items = {}
                for item in CONFIG_ITEM_CODES:
                    val = self._gen_base_value(member, item)
                    base_items[item] = val
                    db.add(BaseConfig(
                        member_system=member,
                        config_item=item,
                        config_value=val,
                        config_type=CONFIG_ITEM_TYPES[item],
                    ))
                self._base_config[member] = base_items
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"基本构型初始化失败(可能已存在): {e}")
        finally:
            db.close()
        logger.info(f"飞机基本构型报告已初始化: {len(self._base_config)}个成员系统")

    def _gen_base_value(self, member: str, item: str) -> str:
        """确定性生成基本构型值"""
        if item == "MF":
            return f"{member}_MFG"
        if item == "MFR":
            return f"MF{member[-2:] if len(member) >= 2 else member}".upper()
        if item == "PNR":
            return f"{member}-100-01"
        if item == "SER":
            return f"SN-{member}-0001"
        if item == "DMF":
            return "2024-06-30"
        if item == "SW_PN":
            return f"{member}-SW-01"
        if item == "SW_LOC":
            return member
        return f"{item}_{member}"

    def _mutate(self, member: str, item: str, base_val: str) -> str:
        """模拟构型变更 (偏离基本构型)"""
        if item == "SER":
            return f"SN-{member}-{random.randint(100, 999):04d}"
        if item == "SW_PN":
            return f"{member}-SW-{random.randint(2, 99):02d}"
        if item == "DMF":
            return f"20{random.randint(20, 25)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        return f"{base_val}-REV{random.randint(2, 9)}"

    # ==================== 周期接收 ====================

    async def _config_receive_loop(self):
        """周期性接收成员系统构型报告"""
        while self._running:
            try:
                members = random.sample(
                    [m["code"] for m in self._member_catalog],
                    min(self.RECEIVE_BATCH, len(self._member_catalog)),
                )
                for member in members:
                    await self.process_config_report(member)
            except Exception as e:
                logger.error(f"构型接收循环异常: {e}")
            await asyncio.sleep(self.RECEIVE_INTERVAL)

    # ==================== 核心: 构型报告处理 ====================

    async def process_config_report(self, member_system: str) -> dict:
        """处理一条成员系统构型报告:
        1) 与飞机基本构型比对 -> 不一致生成构型错误报告
        2) 与上一周期比对 -> 不一致更新数据库中该成员系统构型报告
        """
        base = self._base_config.get(member_system)
        if not base:
            return {"status": "error", "message": f"成员系统 {member_system} 基本构型不存在"}

        current = self._current_config.setdefault(member_system, dict(base))
        error_report_id = f"CE-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{member_system}"
        mismatch_items = []
        updated_count = 0

        db = SessionLocal()
        try:
            for item, base_val in base.items():
                # 本周期构型值: 小概率发生构型变更 (持久演化, 偏离基本构型)
                if random.random() < self.CHANGE_RATE:
                    current[item] = self._mutate(member_system, item, base_val)
                received = current.get(item, base_val)

                # 1) 与基本构型比对 -> 不一致生成构型错误报告
                if received != base_val:
                    db.add(ConfigErrorReport(
                        error_report_id=error_report_id,
                        member_system=member_system,
                        config_item=item,
                        received_value=received,
                        expected_value=base_val,
                        config_type=CONFIG_ITEM_TYPES[item],
                    ))
                    mismatch_items.append({"item": item, "received": received,
                                           "expected": base_val})

                # 2) 与上一周期比对 -> 不一致更新数据库
                existing = db.query(MemberConfig).filter(
                    MemberConfig.member_system == member_system,
                    MemberConfig.config_item == item,
                ).first()
                if existing:
                    if existing.config_value != received:
                        existing.config_value = received
                        updated_count += 1
                    existing.report_time = datetime.utcnow()
                else:
                    db.add(MemberConfig(
                        member_system=member_system,
                        config_item=item,
                        config_value=received,
                        config_type=CONFIG_ITEM_TYPES[item],
                        report_time=datetime.utcnow(),
                    ))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"构型报告存储失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        if mismatch_items:
            await ws_manager.broadcast("config_mismatch", {
                "member_system": member_system,
                "error_report_id": error_report_id,
                "details": mismatch_items,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.warning(f"构型不一致: {member_system} ({len(mismatch_items)}项)")

        return {"status": "ok", "member": member_system,
                "has_mismatch": bool(mismatch_items),
                "mismatch_count": len(mismatch_items),
                "updated_count": updated_count}

    # ==================== 查询接口 ====================

    def get_member_systems(self) -> dict:
        """获取成员系统列表 (供前端下拉), 含构型报告/错误报告状态"""
        db = SessionLocal()
        try:
            members = []
            for m in self._member_catalog:
                code = m["code"]
                has_report = db.query(MemberConfig.id).filter(
                    MemberConfig.member_system == code).first() is not None
                has_error = db.query(ConfigErrorReport.id).filter(
                    ConfigErrorReport.member_system == code).first() is not None
                members.append({
                    "memberSystem": code,
                    "memberName": m["name"],
                    "ata": m["ata"],
                    "hasReport": has_report,
                    "hasError": has_error,
                })
            return {"total": len(members), "memberList": members}
        finally:
            db.close()

    def get_member_config(self, member_system: str) -> dict:
        """获取某成员系统的构型信息 (当前构型 vs 基本构型, 含一致性)"""
        base = self._base_config.get(member_system)
        if not base:
            return {"status": "error", "message": f"成员系统 {member_system} 基本构型不存在"}
        db = SessionLocal()
        try:
            rows = db.query(MemberConfig).filter(
                MemberConfig.member_system == member_system).all()
            by_item = {r.config_item: r for r in rows}
            items = []
            for item in CONFIG_ITEM_CODES:
                base_val = base[item]
                r = by_item.get(item)
                received = r.config_value if r else base_val
                items.append({
                    "item": item,
                    "itemName": CONFIG_ITEM_NAMES.get(item, item),
                    "configType": CONFIG_ITEM_TYPES[item],
                    "receivedValue": received,
                    "expectedValue": base_val,
                    "match": received == base_val,
                    "reportTime": r.report_time.isoformat() if r and r.report_time else None,
                })
            name = MEMBER_SYSTEM_MAP.get(member_system) or \
                next((m["name"] for m in self._member_catalog if m["code"] == member_system), member_system)
            return {
                "memberSystem": member_system,
                "memberName": name,
                "items": items,
                "matchCount": sum(1 for i in items if i["match"]),
                "totalCount": len(items),
            }
        finally:
            db.close()

    def get_base_config(self, member: Optional[str] = None, page: int = 1,
                        size: int = 20) -> dict:
        """获取飞机基本构型报告"""
        db = SessionLocal()
        try:
            query = db.query(BaseConfig)
            if member:
                query = query.filter(BaseConfig.member_system == member)
            total = query.count()
            rows = query.order_by(BaseConfig.member_system, BaseConfig.config_item) \
                .offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "member_system": r.member_system,
                    "config_item": r.config_item,
                    "config_item_name": CONFIG_ITEM_NAMES.get(r.config_item, r.config_item),
                    "config_value": r.config_value,
                    "config_type": r.config_type,
                } for r in rows],
            }
        finally:
            db.close()

    def get_error_reports(self, member: Optional[str] = None,
                          page: int = 1, size: int = 20) -> dict:
        """获取构型错误报告列表"""
        db = SessionLocal()
        try:
            query = db.query(ConfigErrorReport)
            if member:
                query = query.filter(ConfigErrorReport.member_system == member)
            total = query.count()
            rows = query.order_by(ConfigErrorReport.created_at.desc()) \
                .offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "error_report_id": r.error_report_id,
                    "member_system": r.member_system,
                    "member_name": MEMBER_SYSTEM_MAP.get(r.member_system, r.member_system),
                    "config_item": r.config_item,
                    "config_item_name": CONFIG_ITEM_NAMES.get(r.config_item, r.config_item),
                    "received_value": r.received_value,
                    "expected_value": r.expected_value,
                    "config_type": r.config_type,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                } for r in rows],
            }
        finally:
            db.close()

    # ==================== 维护人员主动请求 ====================

    async def request_member_config(self, member_system: str,
                                    operator: str = "TEST") -> dict:
        """维护人员请求某成员系统构型报告: 发出构型获取指令 -> 成员系统响应 -> 处理并返回"""
        if member_system not in self._base_config:
            return {"status": "error", "message": f"成员系统 {member_system} 基本构型不存在"}
        # 发出获取指令 (模拟成员系统响应构型报告)
        result = await self.process_config_report(member_system)
        config = self.get_member_config(member_system)
        return {**result, "operator": operator, "config": config}

    # ==================== 兼容旧接口 ====================

    def get_config_report(self, db: Session, member: Optional[str] = None,
                          page: int = 1, size: int = 20) -> dict:
        """获取构型报告列表 (基于成员系统当前构型快照)"""
        query = db.query(MemberConfig)
        if member:
            query = query.filter(MemberConfig.member_system == member)
        query = query.order_by(MemberConfig.report_time.desc())
        total = query.count()
        rows = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total, "page": page, "size": size,
            "items": [{
                "id": r.id,
                "member_system": r.member_system,
                "config_item": r.config_item,
                "config_value": r.config_value,
                "config_type": r.config_type,
                "report_time": r.report_time.isoformat() if r.report_time else None,
            } for r in rows],
        }

    def batch_verify(self, db: Session, count: int = 400) -> dict:
        """批量构型验证: 基于基本构型 vs 成员系统当前构型比对"""
        results = {"total": count, "pass": 0, "mismatch": 0, "details": []}
        for m in self._member_catalog[:count]:
            member = m["code"]
            base = self._base_config.get(member, {})
            rows = db.query(MemberConfig).filter(
                MemberConfig.member_system == member).all()
            by_item = {r.config_item: r for r in rows}
            for item, expected in base.items():
                r = by_item.get(item)
                received = r.config_value if r else expected
                if received != expected:
                    results["mismatch"] += 1
                    results["details"].append({
                        "member": member, "item": item,
                        "value": received, "expected": expected,
                    })
                else:
                    results["pass"] += 1
        return results


    # ==================== ACR 飞机构型报告查询 (对齐构型信息.py/config_verify.py) ====================

    def _build_acr_catalog(self):
        """构建 ACR 目录: 为每个真实成员系统分配数字 equipID 与 ATA 章节, 供 ACR*Request 接口查询

        ACR 飞机构型报告仅针对真实成员系统 (虚拟成员用于满足"400成员"构型容量要求,
        不参与 ACR 查询), 避免虚拟成员的伪 ATA 章节污染 ATA 列表。
        """
        catalog = []
        for idx, (code, name, ata) in enumerate(MEMBER_SYSTEMS, start=1):
            # AHMU 自身归属 ATA 45 (机载维护系统), 其余按成员名录 ATA 章节
            ata = acr_icd.AHMU_ATA if code == "AHMU" else ata
            entry = {
                "equipID": 3000 + idx,          # 稳定数字标识 (3001 起递增)
                "ata": str(ata),
                "equipName": name,
                "memberCode": code,
            }
            catalog.append(entry)
        self._acr_catalog = catalog
        self._acr_by_equipid = {e["equipID"]: e for e in catalog}
        self._acr_by_ata = {}
        for e in catalog:
            self._acr_by_ata.setdefault(e["ata"], []).append(e)
        logger.info(f"ACR 构型目录已构建: {len(catalog)} 个设备, "
                    f"{len(self._acr_by_ata)} 个 ATA 章节")

    def _ensure_acr_catalog(self):
        """懒加载 ACR 目录 (服务未 start 时直接查询也可用)"""
        if not self._acr_catalog:
            self._build_acr_catalog()

    # ==================== 构型报告 UDP 接收 (Mock AFDX) ====================

    def _setup_rx_socket(self):
        """创建构型报告 UDP 接收 socket (Mock AFDX 模拟 Config_Report_Dataset 收发)"""
        try:
            self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._rx_sock.bind((self._rx_host, self._rx_port))
            self._rx_sock.setblocking(False)
            logger.info(f"[Config] 构型报告 UDP 接收已监听: {self._rx_host}:{self._rx_port}")
        except OSError as e:
            logger.error(f"[Config] 构型报告 UDP socket 初始化失败: {e}")
            self._close_rx_socket()

    def _close_rx_socket(self):
        if self._rx_sock:
            try:
                self._rx_sock.close()
            except OSError:
                pass
        self._rx_sock = None

    async def _config_report_rx_loop(self):
        """构型报告 UDP 接收循环: 接收成员系统上报的构型报告并入库比对"""
        loop = asyncio.get_event_loop()
        while self._running and self._rx_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._rx_sock, 65535)
                self._handle_config_report(data, addr)
            except asyncio.CancelledError:
                break
            except OSError:
                await asyncio.sleep(0.5)

    def _handle_config_report(self, data: bytes, addr):
        """处理接收到的构型报告 (Mock JSON): 解析四类字段并入库比对"""
        msg = acr_icd.decode_config_report(data)
        if msg is None:
            logger.warning(f"[Config] 无法解析构型报告: {data[:80]!r}")
            return
        norm = acr_icd.normalize_config_report(msg)
        err = acr_icd.validate_config_report(norm)
        if err:
            logger.warning(f"[Config] 构型报告校验失败: {err}")
            return
        self.apply_config_report(norm)

    # ==================== ACR 构型请求接收与响应 (Deal_Type 1=429/2=664) ====================

    def _setup_acr_req_socket(self):
        """创建 ACR 构型请求 UDP 接收 socket (Mock AFDX)"""
        try:
            self._acr_req_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._acr_req_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._acr_req_sock.bind((self._rx_host, self._acr_req_port))
            self._acr_req_sock.setblocking(False)
            logger.info(f"[Config] ACR 构型请求 UDP 接收已监听: {self._rx_host}:{self._acr_req_port}")
        except OSError as e:
            logger.error(f"[Config] ACR 构型请求 UDP socket 初始化失败: {e}")
            self._close_acr_req_socket()

    def _close_acr_req_socket(self):
        if self._acr_req_sock:
            try:
                self._acr_req_sock.close()
            except OSError:
                pass
        self._acr_req_sock = None

    async def _acr_request_rx_loop(self):
        """ACR 构型请求 UDP 接收循环: 接收测试端 ACR 请求并回传构型数据"""
        loop = asyncio.get_event_loop()
        while self._running and self._acr_req_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._acr_req_sock, 65535)
                self._handle_acr_request(data, addr)
            except asyncio.CancelledError:
                break
            except OSError:
                await asyncio.sleep(0.5)

    def _handle_acr_request(self, data: bytes, addr):
        """处理 ACR 构型请求: 解析 MSID -> 找成员 -> 构型数据 -> 回传响应"""
        msg = acr_icd.decode_acr_request(data)
        if msg is None:
            logger.warning(f"[Config] 无法解析 ACR 构型请求: {data[:80]!r}")
            return
        norm = acr_icd.normalize_acr_request(msg)
        result = self.handle_acr_request(norm)
        # UDP 回传响应
        if addr and result.get("status") == "ok":
            self._send_acr_response(addr, result)

    def handle_acr_request(self, norm: dict) -> dict:
        """处理一条 ACR 构型请求: 解析 MSID -> 定位成员 -> 组装构型数据响应

        norm: normalize_acr_request 的输出 (deal_type/msid/hwpn/ahwsn/action)
        返回 {status, member_system, deal_type, hard, specific, soft, soft_parts}
        """
        msid = norm["msid"]
        entry = self._resolve_acr_entry(msid)
        if not entry:
            return {"status": "error", "message": f"成员系统不存在: {msid}"}
        member = entry["memberCode"]
        # 优先返回经构型报告接收的当前构型, 无报告回退基本构型
        rep = self._get_acr_report(member)
        if rep:
            hard = {
                "hardPartNumber": rep["hardPartNumber"],
                "hardPartDescription": rep["hardPartDescription"],
                "hardSerialNumber": rep["hardSerialNumber"],
                "hardModificationStatus": rep["hardModificationStatus"],
            }
            specific = rep["specificData"]
            soft = {"softLocationID": rep["softLocationID"],
                    "softLocationDescription": rep["softLocationDescription"]}
            soft_parts = rep["softParts"]
        else:
            hard = acr_icd.gen_hard_info(member, entry["equipName"])
            specific = acr_icd.gen_specific_data(member)
            soft = acr_icd.gen_soft_info(member, entry["equipName"])
            soft_parts = acr_icd.gen_soft_parts(member)
        return {
            "status": "ok", "member_system": member, "deal_type": norm["deal_type"],
            "hard": hard, "specific": specific, "soft": soft, "soft_parts": soft_parts,
        }

    def _send_acr_response(self, addr, result: dict):
        """经 UDP 回传 ACR 构型数据响应给请求方"""
        try:
            data = acr_icd.encode_acr_response(
                result["member_system"], result["deal_type"],
                result["hard"], result["specific"], result["soft"], result["soft_parts"])
            self._acr_req_sock.sendto(data, addr)
            logger.info(f"[Config] 已回传 ACR 响应: {result['member_system']} -> {addr}")
        except OSError as e:
            logger.error(f"[Config] ACR 响应回传失败: {e}")

    def apply_config_report(self, norm: dict) -> dict:
        """应用一条构型报告: 解析四类字段 -> 入库 -> 与基本构型比对 -> 错误报告

        norm: normalize_config_report 的输出 (member_system + 四类字段)
        返回 {status, member, mismatch_count}
        """
        member = norm["member_system"]
        name = MEMBER_SYSTEM_MAP.get(member, member)
        # 基本构型 (确定性 gen_* 生成)
        base_hard = acr_icd.gen_hard_info(member, name)
        base_soft = acr_icd.gen_soft_info(member, name)
        base_parts = acr_icd.gen_soft_parts(member)

        db = SessionLocal()
        try:
            # 1. upsert AcrConfigReport
            rec = db.query(AcrConfigReport).filter(
                AcrConfigReport.member_system == member).first()
            if rec is None:
                rec = AcrConfigReport(member_system=member, equip_id=norm.get("equip_id"))
                db.add(rec)
            rec.hard_part_number = norm["hard_part_number"]
            rec.hard_part_description = norm["hard_part_description"]
            rec.hard_serial_number = norm["hard_serial_number"]
            rec.hard_modification_status = norm["hard_modification_status"]
            rec.specific_data = json.dumps(norm["specific_data"], ensure_ascii=False)
            rec.soft_location_id = norm["soft_location_id"]
            rec.soft_location_description = norm["soft_location_description"]
            rec.soft_parts = json.dumps(norm["soft_parts"], ensure_ascii=False)
            rec.report_time = datetime.utcnow()

            # 2. 与基本构型比对 -> 不一致生成构型错误报告
            error_report_id = f"CE-ACR-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{member}"
            mismatch_items = []
            checks = [
                ("hardPartNumber", norm["hard_part_number"], base_hard["hardPartNumber"]),
                ("hardSerialNumber", norm["hard_serial_number"], base_hard["hardSerialNumber"]),
                ("hardModificationStatus", norm["hard_modification_status"],
                 base_hard["hardModificationStatus"]),
                ("softLocationID", norm["soft_location_id"], base_soft["softLocationID"]),
            ]
            for item, received, expected in checks:
                if received and received != expected:
                    db.add(ConfigErrorReport(
                        error_report_id=error_report_id,
                        member_system=member,
                        config_item=item,
                        received_value=received,
                        expected_value=expected,
                        config_type="hardware" if "hard" in item.lower() or "Serial" in item else "software",
                    ))
                    mismatch_items.append({"item": item, "received": received, "expected": expected})
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[Config] 构型报告存储失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        if mismatch_items:
            logger.warning(f"[Config] 构型不一致: {member} ({len(mismatch_items)}项)")
        return {"status": "ok", "member": member,
                "has_mismatch": bool(mismatch_items),
                "mismatch_count": len(mismatch_items)}

    def _get_acr_report(self, member_code: str) -> Optional[dict]:
        """从 AcrConfigReport 读取某成员的构型报告 (无记录返回 None)"""
        db = SessionLocal()
        try:
            rec = db.query(AcrConfigReport).filter(
                AcrConfigReport.member_system == member_code).first()
            if rec is None:
                return None
            return {
                "hardPartNumber": rec.hard_part_number,
                "hardPartDescription": rec.hard_part_description,
                "hardSerialNumber": rec.hard_serial_number,
                "hardModificationStatus": rec.hard_modification_status,
                "specificData": json.loads(rec.specific_data or "[]"),
                "softLocationID": rec.soft_location_id,
                "softLocationDescription": rec.soft_location_description,
                "softParts": json.loads(rec.soft_parts or "[]"),
                "reportTime": rec.report_time.isoformat() if rec.report_time else None,
            }
        finally:
            db.close()

    def _colour_of(self, member_code: str) -> int:
        """判定构型一致性颜色: 1正常 / 2变更 / 3不匹配"""
        db = SessionLocal()
        try:
            has_error = db.query(ConfigErrorReport.id).filter(
                ConfigErrorReport.member_system == member_code).first() is not None
            if has_error:
                return acr_icd.COLOUR_MISMATCH
            # 有无构型变更 (当前构型与基本构型不一致)
            rows = db.query(MemberConfig).filter(
                MemberConfig.member_system == member_code).all()
            base = self._base_config.get(member_code)
            if base:
                by_item = {r.config_item: r for r in rows}
                for item, base_val in base.items():
                    r = by_item.get(item)
                    if r and r.config_value != base_val:
                        return acr_icd.COLOUR_CHANGED
            return acr_icd.COLOUR_NORMAL
        finally:
            db.close()

    def get_acr_atas(self) -> list:
        """ACRATAsRequest: ATA 列表 -> [ {acrAtaStructList: [{ata, ataName}]} ]"""
        self._ensure_acr_catalog()
        atas = []
        seen = set()
        for ata in self._acr_by_ata:
            if ata in seen:
                continue
            seen.add(ata)
            atas.append({"ata": ata, "ataName": acr_icd.ata_name(ata)})
        # 按 ATA 章节号升序
        atas.sort(key=lambda x: int(x["ata"]) if x["ata"].isdigit() else 999)
        return [{"acrAtaStructList": atas}]

    def get_acr_equips(self, ata: str, page: int = 1, size: int = 20) -> list:
        """ACREquipsRequest: 某 ATA 下设备名列表 -> [ {totalCount, equipNameList} ]"""
        self._ensure_acr_catalog()
        ata = str(ata or "").strip().lstrip("ata").lstrip("ATA")
        items = self._acr_by_ata.get(ata, [])
        total = len(items)
        start = (page - 1) * size
        page_items = items[start:start + size]
        return [{
            "totalCount": total,
            "equipNameList": [{"equipName": e["equipName"], "equipID": e["equipID"]}
                              for e in page_items],
        }]

    def _resolve_acr_entry(self, equip_id) -> dict:
        """按 equipID 解析 ACR 目录项 (支持 int / 数字字符串 / 成员 code 回退)"""
        self._ensure_acr_catalog()
        if isinstance(equip_id, str) and equip_id.strip().isdigit():
            equip_id = int(equip_id.strip())
        entry = None
        if isinstance(equip_id, int):
            entry = self._acr_by_equipid.get(equip_id)
        if entry is None and isinstance(equip_id, str):
            entry = next((e for e in self._acr_catalog if e["memberCode"] == equip_id), None)
        return entry

    def get_acr_hard_info(self, equip_id) -> list:
        """ACRHardInfoRequest: 硬件信息 -> [ {colour, state, hardPartNumber, ...} ]

        优先返回经 AFDX 接收的构型报告值, 无报告时回退确定性基本构型。
        """
        entry = self._resolve_acr_entry(equip_id)
        if not entry:
            return [{"error": f"设备不存在: {equip_id}"}]
        member = entry["memberCode"]
        rep = self._get_acr_report(member)
        if rep:
            hard = {
                "hardPartNumber": rep["hardPartNumber"],
                "hardPartDescription": rep["hardPartDescription"],
                "hardSerialNumber": rep["hardSerialNumber"],
                "hardModificationStatus": rep["hardModificationStatus"],
            }
        else:
            hard = acr_icd.gen_hard_info(member, entry["equipName"])
        hard["colour"] = self._colour_of(member)
        hard["state"] = acr_icd.CONFIG_STATE_CURRENT
        return [hard]

    def get_acr_specific(self, equip_id, page: int = 1, size: int = 15) -> list:
        """ACRSpecificDataRequest: 特定数据列表 -> [ {specificInfoList: [{colour, specificData}]} ]"""
        entry = self._resolve_acr_entry(equip_id)
        if not entry:
            return [{"specificInfoList": []}]
        member = entry["memberCode"]
        colour = self._colour_of(member)
        rep = self._get_acr_report(member)
        data = rep["specificData"] if rep else acr_icd.gen_specific_data(member)
        start = (page - 1) * size
        return [{"specificInfoList": [
            {"colour": colour, "specificData": d} for d in data[start:start + size]
        ]}]

    def get_acr_soft_info(self, equip_id) -> list:
        """ACRSoftInfoRequest: 软件信息 -> [ {colour, softLocationID, softLocationDescription} ]"""
        entry = self._resolve_acr_entry(equip_id)
        if not entry:
            return [{"error": f"设备不存在: {equip_id}"}]
        member = entry["memberCode"]
        rep = self._get_acr_report(member)
        if rep:
            soft = {
                "softLocationID": rep["softLocationID"],
                "softLocationDescription": rep["softLocationDescription"],
            }
        else:
            soft = acr_icd.gen_soft_info(member, entry["equipName"])
        soft["colour"] = self._colour_of(member)
        return [soft]

    def get_acr_soft_part(self, equip_id, page: int = 1, size: int = 10) -> list:
        """ACRSoftPartResponse: 软件部件列表 -> [ {acrSoftPartStructList: [{colour, ...}]} ]"""
        entry = self._resolve_acr_entry(equip_id)
        if not entry:
            return [{"acrSoftPartStructList": []}]
        member = entry["memberCode"]
        colour = self._colour_of(member)
        rep = self._get_acr_report(member)
        parts = rep["softParts"] if rep else acr_icd.gen_soft_parts(member)
        start = (page - 1) * size
        return [{"acrSoftPartStructList": [
            {"colour": colour, **p} for p in parts[start:start + size]
        ]}]


# 全局单例
config_service = ConfigManagementService()
