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
import struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, LifecycleData, LifecycleRetrievalLog
from app.core.websocket_manager import ws_manager
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


class LifecycleService:
    """生命周期管理服务 - 成员系统生命周期数据管理"""

    def __init__(self):
        self._running = False

    async def start(self):
        """启动服务, 初始化成员系统生命周期数据"""
        self._running = True
        self._init_member_systems()
        logger.info("生命周期管理服务已启动")

    async def stop(self):
        self._running = False
        logger.info("生命周期管理服务已停止")

    # ==================== 成员系统生命周期数据 ====================

    def _init_member_systems(self):
        """初始化成员系统生命周期数据 (LifecycleData 表)
        每个真实成员系统 (如 HF_HSCU) 一条记录, 模拟已有的上电运行时间/循环上电循环计数
        """
        db = SessionLocal()
        try:
            existing = db.query(LifecycleData).count()
            if existing > 0:
                logger.info(f"成员系统生命周期数据已存在: {existing}条, 跳过初始化")
                return

            for code, name, ata_code in MEMBER_SYSTEMS:
                power_on_time = random.randint(3600, 500000)
                power_cycle_count = random.randint(1, 500)
                rec = LifecycleData(
                    member_system=code,
                    power_on_time=power_on_time,
                    power_cycle_count=power_cycle_count,
                    last_retrieved=datetime.utcnow() - timedelta(hours=random.randint(1, 72)),
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
                })

            return {"messageID": 2310, "total": len(members), "memberList": members}
        finally:
            db.close()

    async def retrieve_member(self, member_code: str, operator: str = "TEST") -> dict:
        """查看成员系统生命周期信息: 向该成员系统发出生命周期获取指令, 并返回其生命周期信息
        模拟ARINC A429通信: 发送获取命令 → 成员系统响应 → 更新上电运行时间/循环上电循环计数
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
                "timestamp": datetime.utcnow().isoformat(),
            })

            # 发送获取命令 (A429 Label227=0xE9, cmd=0x8)
            logger.info(f"[TimeCycle] 向成员系统 {member_code}({name}) 发送生命周期获取指令")
            try:
                cmd_word = struct.pack(">I", 0xE9000008)
                logger.debug(f"[TimeCycle] A429命令字: {cmd_word.hex()}")
            except Exception:
                pass

            # 模拟成员系统响应延迟
            await asyncio.sleep(2.0)

            old_time = rec.power_on_time or 0
            old_count = rec.power_cycle_count or 0
            new_time = old_time + random.randint(60, 3600)
            new_count = old_count + random.randint(0, 3)
            status_str = self._format_time(new_time)

            rec.power_on_time = new_time
            rec.power_cycle_count = new_count
            rec.last_retrieved = datetime.utcnow()
            rec.retrieval_status = "success"
            db.commit()

            logger.info(f"[TimeCycle] 成员系统 {member_code} 获取成功: 运行{status_str}, 循环上电循环计数{new_count}")

            # 记录获取日志
            self._write_log(member_code, name, member_code, operator, "success",
                            power_on_time=new_time, power_cycle_count=new_count)

            # 推送结果
            await ws_manager.broadcast("lifecycle_retrieved", {
                "member_system": member_code,
                "member_name": name,
                "power_on_time": new_time,
                "power_cycle_count": new_count,
                "status_string": status_str,
                "timestamp": datetime.utcnow().isoformat(),
            })

            return {
                "status": "ok",
                "member_system": member_code,
                "member_name": name,
                "power_on_time": new_time,
                "power_cycle_count": new_count,
                "status_string": status_str,
                "last_retrieved": rec.last_retrieved.isoformat(),
            }
        except Exception as e:
            db.rollback()
            logger.error(f"成员系统 {member_code} 生命周期获取失败: {e}")
            self._write_log(member_code, name, member_code, operator, "failed", error=str(e))
            return {"status": "error", "message": str(e)}
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
                   power_cycle_count: int = 0, error: Optional[str] = None):
        """记录生命周期获取日志 (操作时间/操作用户/被操作的成员系统/操作状态)"""
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
                           page: int = 1, size: int = 20) -> dict:
        """查询生命周期获取日志"""
        query = db.query(LifecycleRetrievalLog)
        if status:
            query = query.filter(LifecycleRetrievalLog.status == status)
        if equip_id:
            query = query.filter(LifecycleRetrievalLog.equip_id == equip_id)
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
