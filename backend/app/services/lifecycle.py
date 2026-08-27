"""
生命周期管理服务 (Time Cycle Management)
对应机载航电 OHMS 时间周期功能:
  - message 2306: TCATAsRequest     → 获取ATA分类列表
  - message 2307: TCEquipsRequest   → 获取设备列表(按ATA)
  - message 2305: TCStatusRequest   → 获取时间周期状态列表
  - tcRetrieval(equipID)            → 触发单个设备时间周期获取
  - TimeCycle_MS                    → ARINC A429/A664 通信流程模拟

ARINC通信流程:
  1. AHMU 通过 A429 (Label227=0xE9, Label230=0x19, cmd=0x8) 或 A664 向成员系统发送获取命令
  2. 成员系统响应上电运行时间 + 上电循环计数
  3. AHMU 更新状态 (status字段含':'时间格式 "HH:MM:SS")
"""
import asyncio
import random
import struct
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import SessionLocal, TCEquipment, LifecycleData
from app.core.websocket_manager import ws_manager
from app.services.maintenance_mode import maintenance_service
from app.core.arinc_mock import hardware

# 标准ATA章节 (航空运输协会 ATA Chapter标准)
ATA_CHAPTERS = [
    ("21", "Air Conditioning"),
    ("22", "Auto Flight"),
    ("23", "Communications"),
    ("24", "Electrical Power"),
    ("25", "Equipment & Furnishings"),
    ("26", "Fire Protection"),
    ("27", "Flight Controls"),
    ("28", "Fuel"),
    ("29", "Hydraulic Power"),
    ("30", "Ice & Rain Protection"),
    ("31", "Indicating & Recording"),
    ("32", "Landing Gear"),
    ("33", "Lights"),
    ("34", "Navigation"),
    ("35", "Oxygen"),
    ("36", "Pneumatic"),
    ("38", "Water & Waste"),
    ("42", "Integrated Avionics"),
    ("44", "Cabin Systems"),
    ("45", "Central Maintenance System"),
    ("46", "Information Systems"),
    ("49", "Airborne Auxiliary Power"),
    ("52", "Doors"),
    ("53", "Fuselage"),
    ("56", "Windows"),
    ("57", "Wings"),
    ("71", "Power Plant"),
    ("73", "Engine Fuel & Control"),
    ("74", "Ignition"),
    ("75", "Engine Air"),
    ("76", "Engine Controls"),
    ("77", "Engine Indicating"),
    ("79", "Engine Oil"),
    ("80", "Engine Starting"),
]

# 设备名称模板 (按ATA章节生成有意义的设备名)
EQUIP_NAME_TEMPLATES = {
    "21": ["Cabin Temp Controller", "Pack Temp Sensor", "Air Cond ValveCtrl", "Zone Temp Sensor"],
    "22": ["Autopilot Computer", "Flight Director", "Yaw Damper", "Auto Thrust"],
    "23": ["VHF Transceiver", "HF Radio", "ACARS MU", "SATCOM SDU", "Audio Mgmt Unit"],
    "24": ["Generator Controller", "Battery Charger", "Power Dist Unit", "RAT Generator"],
    "25": ["Passenger Service Unit", "Cabin Intercomm", "Seat Actuator Ctrl"],
    "26": ["Fire Detector", "Extinguisher Bottle", "Cargo Fire Control"],
    "27": ["Flap Control Computer", "Slat PCU", "Aileron Servo", "Rudder Pedal"],
    "28": ["Fuel Qty Indicator", "Fuel Pump Controller", "Refuel Valve", "Fuel Flow Transducer"],
    "29": ["Hydraulic Pump", "Reservoir Press Sensor", "Filter Clog Indicator"],
    "30": ["Pitot Heat Controller", "Wing Anti-Ice Valve", "Windshield Heat Ctrl"],
    "31": ["Flight Recorder", "Data Mgmt Unit", "Clock System", "Maintenance Panel"],
    "32": ["Brake Control Unit", "Nose Wheel Steering", "Gear Position Sensor", "Tire Press Monitor"],
    "33": ["Ext Light Controller", "Cabin Light Dimmer", "Beacon Light Control"],
    "34": ["IRS Unit", "Radio Altimeter", "TCAS Computer", "Weather Radar", "GPS Receiver"],
    "35": ["Oxygen Mask Controller", "Cabin Press Sensor", "Oxy Cylinder Regulator"],
    "36": ["Bleed Air Controller", "Pre-Cooler Valve", "Isolation Valve Ctrl"],
    "38": ["Waste Tank Sensor", "Water Press Regulator"],
    "42": ["Display Management Computer", "Data Concentrator", "AHMU"],
    "44": ["Cabin Management System", "PA Amplifier", "EVAC Audio Panel"],
    "45": ["CMC Computer", "Centralized Fault Card", "Maintenance Terminal"],
    "46": ["Network Server", "Wireless LAN Unit", "Data Load Router"],
    "49": ["APU Controller", "APU EGT Monitor", "APU Fuel Pump"],
    "52": ["Door Control Unit", "Cargo Door Sensor", "Entry Door Actuator"],
    "53": ["Fuselage Strain Sensor", "Pressure Bulkhead Monitor"],
    "56": ["Window Heat Controller", "Side Window Actuator"],
    "57": ["Wing Strain Sensor", "Wing Tank Probe"],
    "71": ["Engine Controller", "Vibration Monitor", "N1 Sensor", "EGT Probe"],
    "73": ["Fuel Flow Meter", "FCU Controller", "Fuel Manifold Press"],
    "74": ["Ignition Exciter", "Ignition Lead Monitor"],
    "75": ["Bleed Valve Controller", "Turbine Cooling Valve"],
    "76": ["EEC Computer", "Throttle Resolver", "Reverser Deploy Sensor"],
    "77": ["N1 Indicator", "N2 Indicator", "EGT Indicator", "Fuel Flow Indicator"],
    "79": ["Oil Qty Sensor", "Oil Press Indicator", "Oil Debris Monitor"],
    "80": ["Starter Motor", "Ignition Sequence Ctrl"],
}


class LifecycleService:
    """生命周期管理服务 - 时间周期(Time Cycle)管理"""

    def __init__(self):
        self._running = False
        self._retrieval_tasks: dict[str, asyncio.Task] = {}
        self._total_equips = 0

    async def start(self):
        """启动服务, 初始化设备注册表"""
        self._running = True
        self._init_equipment_registry()
        logger.info("生命周期管理服务已启动")

    async def stop(self):
        self._running = False
        for task in self._retrieval_tasks.values():
            task.cancel()
        logger.info("生命周期管理服务已停止")

    # ==================== 初始化 ====================

    def _init_equipment_registry(self):
        """初始化设备注册表
        生成200+设备, 分布在30+ATA章节中
        对应机载航电数据库: MEMBER_SYSTEM + SYS_LRU + MEMBER_SYSTEM_DF
        """
        db = SessionLocal()
        try:
            existing = db.query(TCEquipment).count()
            if existing > 0:
                self._total_equips = existing
                logger.info(f"时间周期设备已存在: {existing}个, 跳过初始化")
                return

            equip_counter = 1
            base_ip_third = 10

            for ata_code, ata_name in ATA_CHAPTERS:
                templates = EQUIP_NAME_TEMPLATES.get(ata_code, [f"LRU-{ata_code}"])
                # 每个ATA章节 5~12 个设备
                num_equips = random.randint(5, 12)

                for i in range(num_equips):
                    equip_id = f"LRU{equip_counter:03d}"
                    member = f"MEM{equip_counter:03d}"

                    # 生成IP地址 (10.x.y.z 格式, 对应AFDX网络)
                    ip = f"10.{base_ip_third}.{equip_counter % 250 + 1}.{equip_counter % 10 + 1}"

                    # 生成端口 (对应MEMBER_SYSTEM_DF中的port_id和rx_port)
                    tx_port = 4000 + equip_counter
                    rx_port = 5000 + equip_counter

                    # 可用性: 85%可获取, 10%不可点击, 5%异常
                    avail_rand = random.random()
                    if avail_rand < 0.05:
                        is_available = 0  # 异常
                    elif avail_rand < 0.15:
                        is_available = 2  # 不可点击
                    else:
                        is_available = 1  # 可获取

                    # 初始生命周期数据 (模拟已有运行时间)
                    power_on_time = random.randint(3600, 500000) if is_available == 1 else 0
                    power_cycle_count = random.randint(1, 500) if is_available == 1 else 0
                    status_string = self._format_time(power_on_time) if power_on_time > 0 else ""

                    equip = TCEquipment(
                        equip_id=equip_id,
                        equip_name=random.choice(templates) + f" #{i + 1}",
                        ata_code=ata_code,
                        ata_name=ata_name,
                        member_system=member,
                        ip_address=ip,
                        rx_port=rx_port,
                        tx_port=tx_port,
                        is_available=is_available,
                        power_on_time=power_on_time,
                        power_cycle_count=power_cycle_count,
                        status_string=status_string,
                        last_retrieved=datetime.utcnow() - timedelta(hours=random.randint(1, 72)) if is_available == 1 else None,
                        retrieval_status="success" if is_available == 1 else "pending",
                    )
                    db.add(equip)
                    equip_counter += 1

            db.commit()
            self._total_equips = equip_counter - 1
            logger.info(f"时间周期设备注册表已初始化: {self._total_equips}个设备, {len(ATA_CHAPTERS)}个ATA章节")
        except Exception as e:
            db.rollback()
            logger.error(f"时间周期设备注册表初始化失败: {e}")
        finally:
            db.close()

    # ==================== 查询接口 ====================

    def get_tcatas(self, page: int = 1, size: int = 50) -> dict:
        """获取ATA分类列表 (对应 message 2306: TCATAsRequest)
        返回格式匹配机载航电: tcEquipStructList
        """
        db = SessionLocal()
        try:
            # 按ATA分组统计设备数
            rows = db.execute(text(
                "SELECT ata_code, ata_name, COUNT(*) as equip_count "
                "FROM tc_equipments GROUP BY ata_code, ata_name "
                "ORDER BY ata_code"
            )).fetchall()

            total = len(rows)
            start = (page - 1) * size
            end = start + size
            page_rows = rows[start:end]

            return {
                "messageID": 2306,
                "total": total,
                "page": page,
                "size": size,
                "tcEquipStructList": [{
                    "ataCode": row[0],
                    "ataName": row[1],
                    "equipCount": row[2],
                } for row in page_rows],
            }
        finally:
            db.close()

    def get_tc_equips(self, ata: Optional[str] = None,
                      page: int = 1, size: int = 16) -> dict:
        """获取设备列表 (对应 message 2307: TCEquipsRequest)
        返回格式匹配机载航电: tcEquipStructList
        每个设备含 equipID / equipName / isAvailable (0=异常, 1=可获取, 2=不可点击)
        """
        db = SessionLocal()
        try:
            query = db.query(TCEquipment)
            if ata:
                query = query.filter(TCEquipment.ata_code == ata)
            query = query.order_by(TCEquipment.equip_id)
            total = query.count()
            items = query.offset((page - 1) * size).limit(size).all()

            return {
                "messageID": 2307,
                "total": total,
                "page": page,
                "size": size,
                "ata": ata,
                "tcEquipStructList": [{
                    "equipID": eq.equip_id,
                    "equipName": eq.equip_name,
                    "isAvailable": eq.is_available,
                    "ataCode": eq.ata_code,
                    "ataName": eq.ata_name,
                    "memberSystem": eq.member_system,
                    "powerOnTime": eq.power_on_time,
                    "powerCycleCount": eq.power_cycle_count,
                    "statusString": eq.status_string or "",
                    "retrievalStatus": eq.retrieval_status,
                    "lastRetrieved": eq.last_retrieved.isoformat() if eq.last_retrieved else None,
                } for eq in items],
            }
        finally:
            db.close()

    def get_tc_status(self, page: int = 1, size: int = 2000,
                      sort_class: int = 1, sort_type: int = 1) -> dict:
        """获取时间周期状态列表 (对应 message 2305: TCStatusRequest)
        返回格式匹配机载航电: tcStatusStructList
        status字段含':'表示时间格式 (HH:MM:SS), 获取成功
        """
        db = SessionLocal()
        try:
            query = db.query(TCEquipment).filter(TCEquipment.power_on_time > 0)
            if sort_class == 1:
                query = query.order_by(
                    TCEquipment.ata_code.asc() if sort_type == 1 else TCEquipment.ata_code.desc()
                )
            else:
                query = query.order_by(
                    TCEquipment.equip_name.asc() if sort_type == 1 else TCEquipment.equip_name.desc()
                )
            total = query.count()
            items = query.offset((page - 1) * size).limit(size).all()

            return {
                "messageID": 2305,
                "total": total,
                "page": page,
                "size": size,
                "tcStatusStructList": [{
                    "equipID": eq.equip_id,
                    "equipName": eq.equip_name,
                    "ataCode": eq.ata_code,
                    "status": eq.status_string or "--:--:--",
                    "powerOnTime": eq.power_on_time,
                    "powerCycleCount": eq.power_cycle_count,
                    "retrievalStatus": eq.retrieval_status,
                    "lastRetrieved": eq.last_retrieved.isoformat() if eq.last_retrieved else None,
                } for eq in items],
            }
        finally:
            db.close()

    # ==================== 时间周期获取 ====================

    async def trigger_retrieval(self, equip_id: str) -> dict:
        """触发单个设备时间周期获取 (对应 tcRetrieval + TimeCycle_MS)
        模拟ARINC通信流程:
          1. 模式校验 (时间周期属"其它业务", 正常模式可操作)
          2. 设备可用性校验 (isAvailable=1才可获取)
          3. 通过A429 Label227/Label230或A664发送获取命令
          4. 成员系统响应 (2s延迟)
          5. 更新 power_on_time / power_cycle_count / status_string
        """
        # 模式校验: 维护模式下仅可操作地面测试与数据加载
        if maintenance_service.current_mode == "maintenance":
            return {"status": "error", "message": "维护模式下仅可操作地面测试与数据加载业务"}

        db = SessionLocal()
        try:
            equip = db.query(TCEquipment).filter(
                TCEquipment.equip_id == equip_id
            ).first()

            if not equip:
                return {"status": "error", "message": f"设备 {equip_id} 不存在"}

            # 可用性校验 (对应 isAvailable: 0=异常, 1=可获取, 2=不可点击)
            if equip.is_available == 2:
                return {"status": "error", "message": f"设备 {equip.equip_name} 不可点击获取"}
            if equip.is_available == 0:
                return {"status": "error", "message": f"设备 {equip.equip_name} 查询异常"}

            # 推送开始事件
            await ws_manager.broadcast("lifecycle_retrieval_start", {
                "equip_id": equip_id,
                "equip_name": equip.equip_name,
                "timestamp": datetime.utcnow().isoformat(),
            })

            # 模拟ARINC命令发送 (A429 Label227=0xE9, Label230=0x19, cmd=0x8)
            logger.info(f"[TimeCycle] 发送获取命令到 {equip_id} ({equip.equip_name}) "
                        f"ATA{equip.ata_code} {equip.ip_address}:{equip.tx_port}")

            # 尝试通过Mock硬件发送 (如果有对应通道)
            try:
                a429_channels = hardware.a429_channels if hasattr(hardware, 'a429_channels') else {}
                # 构造模拟A429命令字 (Label227=0xE9)
                cmd_word = struct.pack(">I", 0xE9000008)
                logger.debug(f"[TimeCycle] A429命令字: {cmd_word.hex()}")
            except Exception:
                pass  # Mock模式, 不影响逻辑

            # 模拟成员系统响应延迟 (对应 etimer.delay(2*1000))
            await asyncio.sleep(2.0)

            # 模拟成员系统返回数据
            old_time = equip.power_on_time or 0
            old_count = equip.power_cycle_count or 0
            new_time = old_time + random.randint(60, 3600)
            new_count = old_count + random.randint(0, 3)
            status_str = self._format_time(new_time)

            # 更新数据库
            equip.power_on_time = new_time
            equip.power_cycle_count = new_count
            equip.status_string = status_str
            equip.last_retrieved = datetime.utcnow()
            equip.retrieval_status = "success"
            db.commit()

            logger.info(f"[TimeCycle] {equip_id} 获取成功: 运行{status_str}, 循环{new_count}次")

            # 推送结果
            await ws_manager.broadcast("lifecycle_retrieved", {
                "equip_id": equip_id,
                "equip_name": equip.equip_name,
                "power_on_time": new_time,
                "power_cycle_count": new_count,
                "status_string": status_str,
                "timestamp": datetime.utcnow().isoformat(),
            })

            return {
                "status": "ok",
                "equip_id": equip_id,
                "equip_name": equip.equip_name,
                "ata_code": equip.ata_code,
                "power_on_time": new_time,
                "power_cycle_count": new_count,
                "status_string": status_str,
            }
        except Exception as e:
            db.rollback()
            logger.error(f"时间周期获取失败 {equip_id}: {e}")

            # 更新失败状态
            try:
                equip.retrieval_status = "failed"
                db.commit()
            except Exception:
                pass

            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    async def batch_retrieve(self, count: int = 200) -> dict:
        """批量获取时间周期数据 (200个成员系统)
        串行获取, 每个设备间隔50ms, 推送进度
        """
        if maintenance_service.current_mode == "maintenance":
            return {"status": "error", "message": "维护模式下仅可操作地面测试与数据加载业务"}

        db = SessionLocal()
        try:
            # 获取可获取的设备列表
            equips = db.query(TCEquipment).filter(
                TCEquipment.is_available == 1
            ).order_by(TCEquipment.equip_id).limit(count).all()
            equip_ids = [eq.equip_id for eq in equips]
        finally:
            db.close()

        total = len(equip_ids)
        results = {"total": total, "success": 0, "failed": 0, "details": []}

        await ws_manager.broadcast("lifecycle_batch_start", {
            "total": total,
            "timestamp": datetime.utcnow().isoformat(),
        })

        for i, equip_id in enumerate(equip_ids, 1):
            result = await self.trigger_retrieval(equip_id)
            if result["status"] == "ok":
                results["success"] += 1
                results["details"].append({
                    "equip_id": equip_id,
                    "power_on_time": result["power_on_time"],
                    "power_cycle_count": result["power_cycle_count"],
                    "status_string": result["status_string"],
                })
            else:
                results["failed"] += 1

            # 推送进度 (每10个或最后一个)
            if i % 10 == 0 or i == total:
                await ws_manager.broadcast("lifecycle_batch_progress", {
                    "total": total,
                    "completed": i,
                    "progress": round(i / total * 100, 1),
                    "timestamp": datetime.utcnow().isoformat(),
                })

            await asyncio.sleep(0.05)  # 轮询间隔

        await ws_manager.broadcast("lifecycle_batch_completed", {
            "total": total,
            "success": results["success"],
            "failed": results["failed"],
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(f"时间周期批量获取完成: {results['success']}/{results['total']}")
        return results

    # ==================== 旧版兼容接口 ====================

    async def retrieve_lifecycle(self, member_system: str) -> dict:
        """获取单个成员系统生命周期数据 (旧版兼容)
        内部映射到 equip_id
        """
        db = SessionLocal()
        try:
            equip = db.query(TCEquipment).filter(
                TCEquipment.member_system == member_system
            ).first()
            if equip:
                equip_id = equip.equip_id
            else:
                # 如果不存在, 直接用member_system作为equip_id
                equip_id = member_system
        finally:
            db.close()
        return await self.trigger_retrieval(equip_id)

    def get_lifecycle_data(self, db: Session, member: Optional[str] = None,
                           page: int = 1, size: int = 20) -> dict:
        """获取生命周期数据列表 (旧版兼容)"""
        query = db.query(TCEquipment)
        if member:
            query = query.filter(
                (TCEquipment.member_system == member) |
                (TCEquipment.equip_id == member)
            )
        query = query.order_by(TCEquipment.last_retrieved.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": eq.id,
                "member_system": eq.member_system,
                "equip_id": eq.equip_id,
                "equip_name": eq.equip_name,
                "ata_code": eq.ata_code,
                "power_on_time": eq.power_on_time,
                "power_cycle_count": eq.power_cycle_count,
                "status_string": eq.status_string,
                "retrieval_status": eq.retrieval_status,
                "last_retrieved": eq.last_retrieved.isoformat() if eq.last_retrieved else None,
            } for eq in items],
        }

    # ==================== 工具函数 ====================

    @staticmethod
    def _format_time(seconds: int) -> str:
        """将秒数格式化为 HH:MM:SS (对应机载航电 status字段含':'格式)"""
        if seconds <= 0:
            return "00:00:00"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"


# 全局单例
lifecycle_service = LifecycleService()
