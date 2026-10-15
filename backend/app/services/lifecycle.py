"""
生命周期管理服务 (Lifecycle Management)
对应技术方案 4.3.11 生命周期管理功能:
  - 维护模式下获取成员系统生命周期数据 (上电运行时间 + 上电循环计数)
  - 支持对生命周期数据进行存储与显示
  - 生成生命周期获取日志 (操作时间/操作用户/被操作的成员系统/操作状态)

交互流程 (对应 SEQ 图 73):
  1. 在生命周期页面选择成员系统 (如 HF_HSCU), 点击"查看成员系统生命周期信息"
  2. AHMU 通过 A429 (Label227=0xE9) 向成员系统发送生命周期获取指令
  3. 成员系统响应上电运行时间 + 上电循环计数
  4. AHMU 更新并展示, 记录获取日志
"""
import asyncio
import json
import random
import socket
import struct
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, LifecycleData, LifecycleRetrievalLog
from app.core.websocket_manager import ws_manager
from app.core.icd_time_cycle import (
    pack_time_cycle_status,
    unpack_time_cycle_status,
    unpack_time_cycle_message,
    TIME_CYCLE_MSG,
)
from app.services.maintenance_mode import maintenance_service
from app.config import DATA_DIR

# 生命周期数据默认存储目录 (用户可自定义路径)
LIFECYCLE_STORAGE_DIR = DATA_DIR / "lifecycle_storage"

# ==================== 成员系统名录 ====================
# 真实航电成员系统 (Member System) 缩写 + 名称 + 所属 ATA 章节
# 用于"查看成员系统生命周期信息"的成员系统下拉列表
MEMBER_SYSTEMS = [
    # (成员系统标识, 中文名称, ATA章节)
    ("HF_HSCU", "高频收发机控制器", "23"),
    ("HF_L", "高频收发机-左", "23"),
    ("HF_R", "高频收发机-右", "23"),
    ("VHF_L", "甚高频收发机-左", "23"),
    ("VHF_R", "甚高频收发机-右", "23"),
    ("VHF_C", "甚高频收发机-中央", "23"),
    ("ACARS_MU", "ACARS管理单元", "23"),
    ("SATCOM_SDU", "卫星通信数据单元", "23"),
    ("AMU", "音频管理单元", "23"),
    ("ADIRU_1", "大气数据惯性基准-1", "34"),
    ("ADIRU_2", "大气数据惯性基准-2", "34"),
    ("ADIRU_3", "大气数据惯性基准-3", "34"),
    ("IRS_1", "惯性基准系统-1", "34"),
    ("IRS_2", "惯性基准系统-2", "34"),
    ("RA_1", "无线电高度表-1", "34"),
    ("RA_2", "无线电高度表-2", "34"),
    ("TCAS", "交通告警与防撞系统", "34"),
    ("GPS_1", "全球定位系统-1", "34"),
    ("WXR", "气象雷达", "34"),
    ("FCC_1", "飞行控制计算机-1", "22"),
    ("FCC_2", "飞行控制计算机-2", "22"),
    ("HF_FCM_1", "飞控模块-1", "27"),
    ("HF_FCM_2", "飞控模块-2", "27"),
    ("HF_FCM_3", "飞控模块-3", "27"),
    ("FMGC_1", "飞行管理引导计算机-1", "22"),
    ("FMGC_2", "飞行管理引导计算机-2", "22"),
    ("SFCC_1", "缝翼襟翼控制计算机-1", "27"),
    ("SFCC_2", "缝翼襟翼控制计算机-2", "27"),
    ("SEC_1", "扰流板升降舵计算机-1", "27"),
    ("ELAC_1", "升降舵副翼计算机-1", "27"),
    ("DMC_1", "显示管理计算机-1", "42"),
    ("DMC_2", "显示管理计算机-2", "42"),
    ("DMC_3", "显示管理计算机-3", "42"),
    ("CDN_1", "核心数据网络-1", "42"),
    ("AHMU", "航电健康管理单元", "42"),
    ("CMC_1", "中央维护计算机-1", "45"),
    ("CMC_2", "中央维护计算机-2", "45"),
    ("EEC_1", "发动机电子控制器-1", "71"),
    ("EEC_2", "发动机电子控制器-2", "71"),
    ("EVMU", "发动机振动监控单元", "71"),
    ("FQIC", "燃油量指示计算机", "28"),
    ("GCU_1", "发电机控制单元-1", "24"),
    ("GCU_2", "发电机控制单元-2", "24"),
    ("APU_ECB", "APU电子控制盒", "49"),
    ("DFDR", "数字飞行数据记录器", "31"),
    ("FDIU", "飞行数据接口单元", "31"),
    ("ACSC_1", "空调系统控制器-1", "21"),
    ("BSCU_1", "刹车系统控制单元-1", "32"),
    ("WHC_1", "风挡加热控制器-1", "30"),
    ("FDU_1", "火警探测单元-1", "26"),
    ("ESU_1", "发动机起动单元-1", "80"),
]

# 成员系统标识 → 名称 映射
MEMBER_SYSTEM_MAP = {code: name for code, name, _ in MEMBER_SYSTEMS}

# ==================== 数据来源标识 (统一) ====================
# 生命周期数据的两类来源:
#   simulated - 模拟数据: AHMU 内部模拟成员系统响应 (无外部设备交互)
#   real      - 真实数据: AHMU 发送指令并与外部设备实际交互后生成
DATA_SOURCE_SIMULATED = "simulated"
DATA_SOURCE_REAL = "real"

DATA_SOURCE_LABELS = {
    DATA_SOURCE_SIMULATED: "模拟",
    DATA_SOURCE_REAL: "真实",
}

# 飞控模块 (具备真实 UDP 链路的成员系统): 这些成员走"真实数据"路径
REAL_MEMBER_SYSTEMS = {"HF_FCM_1", "HF_FCM_2", "HF_FCM_3"}

# 飞控模块生命周期获取指令 (UDP) 配置
# 数据端口 31696 为 AHMU 监听 (飞控模块上报), 指令端口 31697 为飞控模块监听 (AHMU 下发获取指令)
FCM_COMMAND_HOST = "127.0.0.1"
FCM_COMMAND_PORT = 31697


class LifecycleService:
    """生命周期管理服务 - 成员系统生命周期数据管理"""

    def __init__(self):
        self._running = False
        self._last_command_sent_at = None     # 最近一次真实"生命周期获取指令"发送时间 (关联 UDP 响应)
        self._last_command_operator = None    # 最近一次真实"生命周期获取指令"触发者

    async def start(self):
        """启动服务, 初始化成员系统生命周期数据"""
        self._running = True
        self._init_member_systems()
        self._backfill_data_source()
        logger.info("生命周期管理服务已启动")

    async def stop(self):
        self._running = False
        logger.info("生命周期管理服务已停止")

    def _backfill_data_source(self):
        """回填历史记录的数据来源标识 (旧记录 data_source 为 NULL → simulated)"""
        db = SessionLocal()
        try:
            db.query(LifecycleData).filter(LifecycleData.data_source.is_(None)).update(
                {"data_source": DATA_SOURCE_SIMULATED}, synchronize_session=False)
            db.query(LifecycleRetrievalLog).filter(LifecycleRetrievalLog.data_source.is_(None)).update(
                {"data_source": DATA_SOURCE_SIMULATED}, synchronize_session=False)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"历史数据来源标识回填失败: {e}")
        finally:
            db.close()

    # ==================== 成员系统生命周期数据 ====================

    def _init_member_systems(self):
        """初始化成员系统生命周期数据 (LifecycleData 表)
        每个真实成员系统 (如 HF_HSCU) 一条记录, 模拟已有的上电运行时间/上电循环计数
        """
        db = SessionLocal()
        try:
            existing = db.query(LifecycleData).count()
            if existing > 0:
                # 已有数据: 重新锚定时间基点, 避免重启后首次查看出现异常大跳变
                db.query(LifecycleData).update(
                    {"last_retrieved": datetime.now()}, synchronize_session=False)
                db.commit()
                logger.info(f"成员系统生命周期数据已存在: {existing}条, 已重置时间锚点")
                return

            for code, name, ata_code in MEMBER_SYSTEMS:
                power_on_time = random.randint(3600, 500000)
                power_cycle_count = random.randint(1, 500)
                rec = LifecycleData(
                    member_system=code,
                    power_on_time=power_on_time,
                    power_cycle_count=power_cycle_count,
                    last_retrieved=datetime.now(),  # 时间锚点=当前时刻, 首次查看按真实时长累积
                    retrieval_status="success",
                )
                db.add(rec)

            db.commit()
            logger.info(f"成员系统生命周期数据已初始化: {len(MEMBER_SYSTEMS)}个成员系统")
        except Exception as e:
            db.rollback()
            logger.error(f"成员系统生命周期数据初始化失败: {e}")
        finally:
            db.close()

    def get_member_systems(self) -> dict:
        """获取成员系统列表 (含各自当前生命周期数据)
        用于前端"成员系统"下拉选择
        """
        db = SessionLocal()
        try:
            rows = db.query(LifecycleData).all()
            by_member = {r.member_system: r for r in rows}

            members = []
            for code, name, ata_code in MEMBER_SYSTEMS:
                rec = by_member.get(code)
                power_on_time = rec.power_on_time if rec else 0
                power_cycle_count = rec.power_cycle_count if rec else 0
                members.append({
                    "memberSystem": code,
                    "memberName": name,
                    "ataCode": ata_code,
                    "powerOnTime": power_on_time,
                    "powerCycleCount": power_cycle_count,
                    "statusString": self._format_time(power_on_time) if power_on_time else "--:--:--",
                    "retrievalStatus": rec.retrieval_status if rec else "pending",
                    "lastRetrieved": rec.last_retrieved.isoformat() if rec and rec.last_retrieved else None,
                    "dataSource": rec.data_source if rec else DATA_SOURCE_SIMULATED,
                    "dataSourceLabel": DATA_SOURCE_LABELS.get(
                        rec.data_source, "模拟") if rec else "模拟",
                })

            return {"messageID": 2310, "total": len(members), "memberList": members}
        finally:
            db.close()

    async def retrieve_member(self, member_code: str, operator: str = "TEST") -> dict:
        """查看成员系统生命周期信息: 向该成员系统发出生命周期获取指令, 并返回其生命周期信息

        数据来源分两类:
          - 模拟数据 (simulated): 非飞控模块成员系统, AHMU 内部模拟成员系统响应。
          - 真实数据 (real): 飞控模块 (HF_FCM_1/2/3), AHMU 经 UDP 下发"生命周期获取指令",
            等待外部设备实际响应 (经 UDP 31696 上报), 记录指令发送/设备响应时间与结果。
        """
        name = MEMBER_SYSTEM_MAP.get(member_code, member_code)

        # 模式校验: 仅维护模式下可获取
        if maintenance_service.current_mode != "maintenance":
            self._write_log(member_code, name, member_code, operator, "rejected",
                            error=f"非维护模式, 无法获取生命周期数据 (当前模式: {maintenance_service.current_mode})")
            return {"status": "error",
                    "message": f"非维护模式, 无法获取生命周期数据 (当前模式: {maintenance_service.current_mode})"}

        if member_code not in MEMBER_SYSTEM_MAP:
            self._write_log(member_code, "", member_code, operator, "rejected",
                            error=f"成员系统 {member_code} 不存在")
            return {"status": "error", "message": f"成员系统 {member_code} 不存在"}

        # 数据来源分流: 飞控模块走真实链路, 其余走模拟
        if member_code in REAL_MEMBER_SYSTEMS:
            return await self._retrieve_real(member_code, name, operator)
        return await self._retrieve_simulated(member_code, name, operator)

    async def _retrieve_simulated(self, member_code: str, name: str, operator: str) -> dict:
        """模拟数据路径: AHMU 内部模拟成员系统响应 (无外部设备交互)"""
        db = SessionLocal()
        try:
            rec = db.query(LifecycleData).filter(
                LifecycleData.member_system == member_code
            ).first()
            if not rec:
                rec = LifecycleData(member_system=member_code, power_on_time=0, power_cycle_count=0)
                db.add(rec)

            # 推送开始事件
            await ws_manager.broadcast("lifecycle_retrieval_start", {
                "member_system": member_code,
                "member_name": name,
                "data_source": DATA_SOURCE_SIMULATED,
                "timestamp": datetime.utcnow().isoformat(),
            })

            # 1. AHMU 发送"生命周期获取指令" (A429 命令字, 命令成员系统上报加电时间+上电循环计数)
            command_sent_at = datetime.now()
            logger.info(f"[TimeCycle] 向成员系统 {member_code}({name}) 发送生命周期获取指令 (模拟)")
            cmd_word = struct.pack(">I", 0xE9000008)  # A429 生命周期获取命令字
            logger.debug(f"[TimeCycle] A429命令字: {cmd_word.hex()}")

            # 2. 模拟成员系统响应延迟
            await asyncio.sleep(2.0)
            response_received_at = datetime.now()

            # 3. 成员系统按 ICD Time_Cycle_Status 块响应 (160位 BLK)
            #    上电运行时间按真实墙钟时间累积: 自上次记录以来经过的秒数,
            #    保证连续查看时两次取值的差值 = 两次查看之间的真实时长 (而非随机跳变)。
            old_time = rec.power_on_time or 0
            old_count = rec.power_cycle_count or 0
            anchor = rec.last_retrieved or command_sent_at
            elapsed = max(0, int((response_received_at - anchor).total_seconds()))
            new_time = old_time + elapsed
            # 上电循环计数仅在设备真实上下电时变化, 查询操作不改变 (保持稳定连续)
            new_count = old_count

            # 成员系统打包 Time_Cycle_Status (160位块) 经 FCS_OutputData_TO_OMS_BLK_5 上报
            block = pack_time_cycle_status(new_time, new_count)
            # AHMU 解析 Time_Cycle_Status 块
            parsed = unpack_time_cycle_status(block)
            new_time = parsed["power_on_time"]
            new_count = parsed["power_cycle_count"]
            status_str = self._format_time(new_time)

            logger.info(
                f"[TimeCycle] 收到 {TIME_CYCLE_MSG['name']} (VL {TIME_CYCLE_MSG['vl_id']}, "
                f"{TIME_CYCLE_MSG['size']}字节, {TIME_CYCLE_MSG['period_ms']}ms) [模拟]: "
                f"上电时间 {status_str}, 循环计数 {new_count} (本次累计 +{elapsed}s)")

            rec.power_on_time = new_time
            rec.power_cycle_count = new_count
            rec.last_retrieved = response_received_at  # 本地时间 (作为下次累积的时间锚点)
            rec.retrieval_status = "success"
            rec.data_source = DATA_SOURCE_SIMULATED
            db.commit()

            logger.info(f"[TimeCycle] 成员系统 {member_code} 获取成功[模拟]: 运行{status_str}, 上电循环计数{new_count}")

            # 记录获取日志 (模拟数据)
            self._write_log(member_code, name, member_code, operator, "success",
                            power_on_time=new_time, power_cycle_count=new_count,
                            data_source=DATA_SOURCE_SIMULATED,
                            command_sent_at=command_sent_at,
                            response_received_at=response_received_at,
                            response_result="ok")

            # 推送结果
            await ws_manager.broadcast("lifecycle_retrieved", {
                "member_system": member_code,
                "member_name": name,
                "power_on_time": new_time,
                "power_cycle_count": new_count,
                "status_string": status_str,
                "data_source": DATA_SOURCE_SIMULATED,
                "icd_message": TIME_CYCLE_MSG["name"],
                "vl_id": TIME_CYCLE_MSG["vl_id"],
                "block_hex": block.hex(),
                "timestamp": datetime.utcnow().isoformat(),
            })

            return {
                "status": "ok",
                "member_system": member_code,
                "member_name": name,
                "power_on_time": new_time,
                "power_cycle_count": new_count,
                "status_string": status_str,
                "data_source": DATA_SOURCE_SIMULATED,
                "data_source_label": DATA_SOURCE_LABELS[DATA_SOURCE_SIMULATED],
                "icd_message": TIME_CYCLE_MSG["name"],
                "vl_id": TIME_CYCLE_MSG["vl_id"],
                "message_size": TIME_CYCLE_MSG["size"],
                "block_hex": block.hex(),
                "command_sent_at": command_sent_at.isoformat(),
                "response_received_at": response_received_at.isoformat(),
                "response_result": "ok",
                "last_retrieved": rec.last_retrieved.isoformat(),
            }
        except Exception as e:
            db.rollback()
            logger.error(f"成员系统 {member_code} 生命周期获取失败: {e}")
            self._write_log(member_code, name, member_code, operator, "failed", error=str(e))
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    async def _retrieve_real(self, member_code: str, name: str, operator: str) -> dict:
        """真实数据路径: 发送 UDP 生命周期获取指令, 等待外部设备实际响应 (飞控模块)"""
        # 1. 发送真实"生命周期获取指令" (UDP, 飞控模块监听端口 31697)
        command_sent_at = datetime.now()
        self._last_command_sent_at = command_sent_at
        self._last_command_operator = operator
        cmd_word = struct.pack(">I", 0xE9000008)  # A429 生命周期获取命令字
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(cmd_word, (FCM_COMMAND_HOST, FCM_COMMAND_PORT))
            sock.close()
            logger.info(f"[TimeCycle] 向飞控模块 {member_code}({name}) 发送 UDP 生命周期获取指令 "
                        f"({FCM_COMMAND_HOST}:{FCM_COMMAND_PORT}, {cmd_word.hex()})")
        except Exception as e:
            logger.error(f"[TimeCycle] 飞控模块指令发送失败: {e}")
            self._write_log(member_code, name, member_code, operator, "failed",
                            error=f"指令发送失败: {e}",
                            data_source=DATA_SOURCE_REAL,
                            command_sent_at=command_sent_at,
                            response_result="failed")
            return {"status": "error", "message": f"指令发送失败: {e}"}

        await ws_manager.broadcast("lifecycle_retrieval_start", {
            "member_system": member_code,
            "member_name": name,
            "data_source": DATA_SOURCE_REAL,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # 2. 等待外部设备响应 (后台 UDP 接收器收到后更新 LifecycleData)
        rec = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            db = SessionLocal()
            try:
                r = db.query(LifecycleData).filter(
                    LifecycleData.member_system == member_code).first()
                if (r and r.data_source == DATA_SOURCE_REAL and r.last_retrieved
                        and r.last_retrieved >= command_sent_at):
                    rec = (r.power_on_time, r.power_cycle_count, r.last_retrieved,
                           r.retrieval_status)
                    break
            finally:
                db.close()

        # 3. 超时处理
        if rec is None:
            logger.warning(f"[TimeCycle] 飞控模块 {member_code} 响应超时 (3s)")
            self._write_log(member_code, name, member_code, operator, "timeout",
                            error="等待外部设备响应超时 (3s)",
                            data_source=DATA_SOURCE_REAL,
                            command_sent_at=command_sent_at,
                            response_result="timeout")
            return {"status": "error", "message": "等待外部设备响应超时 (3s)"}

        new_time, new_count, response_received_at, retrieval_status = rec
        status_str = self._format_time(new_time)
        block = pack_time_cycle_status(new_time, new_count)

        logger.info(f"[TimeCycle] 收到飞控模块 {member_code} 真实响应: "
                    f"上电时间 {status_str}, 循环计数 {new_count}, 响应结果 {retrieval_status}")

        await ws_manager.broadcast("lifecycle_retrieved", {
            "member_system": member_code,
            "member_name": name,
            "power_on_time": new_time,
            "power_cycle_count": new_count,
            "status_string": status_str,
            "data_source": DATA_SOURCE_REAL,
            "icd_message": TIME_CYCLE_MSG["name"],
            "vl_id": TIME_CYCLE_MSG["vl_id"],
            "block_hex": block.hex(),
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {
            "status": "ok",
            "member_system": member_code,
            "member_name": name,
            "power_on_time": new_time,
            "power_cycle_count": new_count,
            "status_string": status_str,
            "data_source": DATA_SOURCE_REAL,
            "data_source_label": DATA_SOURCE_LABELS[DATA_SOURCE_REAL],
            "icd_message": TIME_CYCLE_MSG["name"],
            "vl_id": TIME_CYCLE_MSG["vl_id"],
            "message_size": TIME_CYCLE_MSG["size"],
            "block_hex": block.hex(),
            "command_sent_at": command_sent_at.isoformat(),
            "response_received_at": response_received_at.isoformat(),
            "response_result": "received",
            "last_retrieved": response_received_at.isoformat(),
        }

    # ==================== UDP 接收 (飞控模块 Time_Cycle_Status, 端口 31696) ====================

    def start_udp_receiver(self, host: str = "0.0.0.0", port: Optional[int] = None):
        """启动 UDP 接收线程, 监听飞控模块 FCS_OutputData_TO_OMS_BLK_5 (默认 31696)

        对齐 ICD: 飞控模块 HF_FCM_1 经 VL 21162 / DstUDP 31696 周期(400ms)发送
        Time_Cycle_Status (160位 BLK), 本接收器接收后更新 LifecycleData。
        真实 AFDX 通过 VL ID/源 IP 区分发送方, Mock 环境简化为一端口一成员 (31696 → HF_FCM_1)。
        """
        if port is None:
            port = TIME_CYCLE_MSG["port"]
        if getattr(self, "_udp_running", False):
            logger.warning("生命周期 UDP 接收器已在运行")
            return
        self._udp_running = True
        self._udp_thread = threading.Thread(
            target=self._udp_recv_loop, args=(host, port), daemon=True,
            name="lifecycle-udp")
        self._udp_thread.start()
        logger.info(f"生命周期 UDP 接收器已启动: {host}:{port} (飞控模块 Time_Cycle_Status)")

    def stop_udp_receiver(self):
        """停止 UDP 接收线程"""
        self._udp_running = False
        logger.info("生命周期 UDP 接收器已停止")

    def _udp_recv_loop(self, host: str, port: int):
        """UDP 接收循环 (后台线程): 接收 28 字节 FCS_OutputData_TO_OMS_BLK_5 → 更新 LifecycleData"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as e:
            logger.error(f"UDP 端口 {host}:{port} 绑定失败: {e}")
            self._udp_running = False
            sock.close()
            return

        member_system = TIME_CYCLE_MSG["member_system"]  # 端口 31696 → HF_FCM_1
        while self._udp_running:
            try:
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(TIME_CYCLE_MSG["size"] * 2)
                msg = unpack_time_cycle_message(data)
                self._apply_udp_time_cycle(member_system, msg, addr)
            except socket.timeout:
                continue
            except ValueError as e:
                logger.warning(f"UDP 消息解析失败: {e}")
            except Exception as e:
                logger.error(f"UDP 接收异常: {e}")
        sock.close()
        logger.info("生命周期 UDP 接收线程已退出")

    def _apply_udp_time_cycle(self, member_system: str, msg: dict, addr) -> None:
        """将 UDP 收到的 Time_Cycle_Status 更新到 LifecycleData (真实数据路径)

        外部设备经 UDP 上报的真实生命周期数据: 更新数据 + 记录真实数据日志
        (含设备响应时间与结果, 并关联最近一次"生命周期获取指令"发送时间)。
        """
        power_on_time = msg["power_on_time"]
        power_cycle_count = msg["power_cycle_count"]
        received_at = datetime.now()
        command_sent_at = self._last_command_sent_at  # 最近一次指令发送时间 (周期上报时可能为 None)
        operator = self._last_command_operator or "SYSTEM"  # 最近一次指令触发者 (周期上报时为 SYSTEM)
        db = SessionLocal()
        try:
            rec = db.query(LifecycleData).filter(
                LifecycleData.member_system == member_system).first()
            if not rec:
                rec = LifecycleData(member_system=member_system, power_on_time=0, power_cycle_count=0)
                db.add(rec)
            rec.power_on_time = power_on_time
            rec.power_cycle_count = power_cycle_count
            rec.last_retrieved = received_at
            rec.retrieval_status = "success"
            rec.data_source = DATA_SOURCE_REAL
            db.commit()

            logger.info(f"[TimeCycle UDP] 收到 {member_system} 生命周期数据 来自 {addr}: "
                        f"上电时间 {self._format_time(power_on_time)}, 循环计数 {power_cycle_count} [真实]")

            # 记录真实数据日志 (含指令发送/设备响应时间与结果)
            self._write_log(member_system, MEMBER_SYSTEM_MAP.get(member_system, member_system),
                            member_system, operator, "success",
                            power_on_time=power_on_time, power_cycle_count=power_cycle_count,
                            data_source=DATA_SOURCE_REAL,
                            command_sent_at=command_sent_at,
                            response_received_at=received_at,
                            response_result="received")
        except Exception as e:
            db.rollback()
            logger.error(f"UDP 生命周期数据入库失败: {e}")
        finally:
            db.close()

    def save_lifecycle_data(self, storage_path: str,
                            member_system: Optional[str] = None) -> dict:
        """主动存储: 将采集到的成员系统生命周期数据保存到用户选择的路径
        storage_path: 用户指定的存储目录 (绝对路径)
        member_system: 可选, 仅保存指定成员系统; 缺省保存全部
        输出 JSON 文件 lifecycle_data_YYYYMMDD_HHMMSS.json, 返回文件路径与条数
        """
        try:
            path = Path(storage_path.strip()) if storage_path and storage_path.strip() else LIFECYCLE_STORAGE_DIR
            path.mkdir(parents=True, exist_ok=True)

            db = SessionLocal()
            try:
                query = db.query(LifecycleData)
                if member_system:
                    query = query.filter(LifecycleData.member_system == member_system)
                rows = query.order_by(LifecycleData.member_system).all()

                records = [{
                    "memberSystem": r.member_system,
                    "memberName": MEMBER_SYSTEM_MAP.get(r.member_system, r.member_system),
                    "powerOnTime": r.power_on_time,
                    "powerCycleCount": r.power_cycle_count,
                    "statusString": self._format_time(r.power_on_time) if r.power_on_time else "--:--:--",
                    "retrievalStatus": r.retrieval_status,
                    "lastRetrieved": r.last_retrieved.isoformat() if r.last_retrieved else None,
                } for r in rows]
            finally:
                db.close()

            filename = f"lifecycle_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path = path / filename
            payload = {
                "saved_at": datetime.now().isoformat(),
                "count": len(records),
                "member_system": member_system,
                "data": records,
            }
            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            logger.info(f"[TimeCycle] 生命周期数据已存储: {file_path} ({len(records)}条)")
            return {
                "status": "ok",
                "file_path": str(file_path),
                "count": len(records),
            }
        except Exception as e:
            logger.error(f"生命周期数据存储失败: {e}")
            return {"status": "error", "message": f"存储失败: {e}"}

    # ==================== 生命周期获取日志 ====================

    def _write_log(self, equip_id: str, equip_name: str, member_system: str,
                   operator: str, status: str, power_on_time: int = 0,
                   power_cycle_count: int = 0, error: Optional[str] = None,
                   data_source: Optional[str] = None,
                   command_sent_at: Optional[datetime] = None,
                   response_received_at: Optional[datetime] = None,
                   response_result: Optional[str] = None):
        """记录生命周期获取日志 (操作时间/操作用户/被操作的成员系统/操作状态/数据来源)"""
        db = SessionLocal()
        try:
            log = LifecycleRetrievalLog(
                equip_id=equip_id,
                equip_name=equip_name,
                member_system=member_system,
                operator=operator,
                status=status,
                power_on_time=power_on_time,
                power_cycle_count=power_cycle_count,
                error_message=error,
                data_source=data_source or DATA_SOURCE_SIMULATED,
                command_sent_at=command_sent_at,
                response_received_at=response_received_at,
                response_result=response_result,
                operated_at=datetime.now(),  # 本地时间 (供维护人员查看)
            )
            db.add(log)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"记录生命周期获取日志失败: {e}")
        finally:
            db.close()

    def get_retrieval_logs(self, db: Session, status: Optional[str] = None,
                           equip_id: Optional[str] = None,
                           data_source: Optional[str] = None,
                           page: int = 1, size: int = 20) -> dict:
        """查询生命周期获取日志 (支持按数据来源筛选)"""
        query = db.query(LifecycleRetrievalLog)
        if status:
            query = query.filter(LifecycleRetrievalLog.status == status)
        if equip_id:
            query = query.filter(LifecycleRetrievalLog.equip_id == equip_id)
        if data_source:
            query = query.filter(LifecycleRetrievalLog.data_source == data_source)
        total = query.count()
        items = query.order_by(LifecycleRetrievalLog.operated_at.desc()) \
            .offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "equip_id": l.equip_id,
                "equip_name": l.equip_name,
                "member_system": l.member_system,
                "operator": l.operator,
                "status": l.status,
                "power_on_time": l.power_on_time,
                "power_cycle_count": l.power_cycle_count,
                "error_message": l.error_message,
                "data_source": l.data_source or DATA_SOURCE_SIMULATED,
                "data_source_label": DATA_SOURCE_LABELS.get(l.data_source, "模拟"),
                "command_sent_at": l.command_sent_at.isoformat() if l.command_sent_at else None,
                "response_received_at": l.response_received_at.isoformat() if l.response_received_at else None,
                "response_result": l.response_result,
                "operated_at": l.operated_at.isoformat() if l.operated_at else None,
            } for l in items],
        }

    # ==================== 工具函数 ====================

    @staticmethod
    def _format_time(seconds: int) -> str:
        """将秒数格式化为 HH:MM:SS"""
        if seconds <= 0:
            return "00:00:00"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"


# 全局单例
lifecycle_service = LifecycleService()
