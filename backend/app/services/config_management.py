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
import random
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, BaseConfig, MemberConfig, ConfigErrorReport
from app.core.websocket_manager import ws_manager
from app.services.lifecycle import MEMBER_SYSTEMS, MEMBER_SYSTEM_MAP

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

    # ==================== 生命周期 ====================

    async def start(self):
        """启动构型管理服务"""
        self._running = True
        self._build_member_catalog()
        self._init_base_config()
        logger.info("构型管理服务已启动")
        asyncio.create_task(self._config_receive_loop())

    async def stop(self):
        self._running = False
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


# 全局单例
config_service = ConfigManagementService()
