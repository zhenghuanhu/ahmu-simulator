"""
故障诊断服务
- 1Hz频率接收成员系统故障报告
- 级联过滤 + FDE关联修正
- SQLite批量存储 + WebSocket实时推送
"""
import asyncio
import random
import json
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, FaultReport, FailureReport
from app.core.websocket_manager import ws_manager
from app.config import SIMULATION_CONFIG


class FaultDiagnosisService:
    """故障诊断引擎"""

    def __init__(self):
        self._running = False
        self._fault_cascade_map = {}  # 级联关系映射
        self._fde_correlation = {}     # FDE关联关系
        self._current_segment = 0       # 当前航段
        self._flight_phase = 0          # 当前飞行阶段
        self._total_faults = 0

    async def start(self):
        """启动故障诊断服务"""
        self._running = True
        logger.info("故障诊断服务已启动")
        # 启动1Hz故障接收循环
        asyncio.create_task(self._fault_receive_loop())

    async def stop(self):
        self._running = False
        logger.info("故障诊断服务已停止")

    async def _fault_receive_loop(self):
        """1Hz故障接收循环 (Mock数据)"""
        while self._running:
            try:
                # Mock: 随机生成故障报告 (5%概率)
                if random.random() < 0.05:
                    member = f"MEM{random.randint(1, SIMULATION_CONFIG['member_system_count']):03d}"
                    fault_code = random.randint(1, 9999)
                    severity = random.choice(["minor", "minor", "minor", "major", "critical"])
                    await self.process_fault_report(member, fault_code, severity)
            except Exception as e:
                logger.error(f"故障接收循环异常: {e}")
            await asyncio.sleep(1.0)  # 1Hz

    async def process_fault_report(self, member_system: str, fault_code: int,
                                    severity: str = "minor") -> dict:
        """
        处理接收到的故障报告
        1. 数据校验
        2. 级联过滤
        3. FDE关联修正
        4. 存储到SQLite
        5. WebSocket推送
        """
        # 1. 数据校验
        if not member_system or fault_code < 0:
            return {"status": "error", "message": "故障数据校验失败"}

        # 2. 级联过滤 - 检查是否为级联故障
        is_cascaded = False
        parent_fault_id = None
        cascade_key = f"{member_system}:{fault_code}"
        if cascade_key in self._fault_cascade_map:
            is_cascaded = True
            parent_fault_id = self._fault_cascade_map[cascade_key]
            logger.info(f"检测到级联故障: {cascade_key} -> 父故障: {parent_fault_id}")

        # 3. FDE关联修正
        fde_code = None
        fde_key = f"{member_system}:{fault_code}"
        if fde_key in self._fde_correlation:
            fde_code = self._fde_correlation[fde_key]

        # 4. 存储到SQLite
        fault = FaultReport(
            member_system=member_system,
            fault_code=f"{member_system}-{fault_code}",
            fault_text=f"成员系统{member_system}故障: 代码{fault_code}",
            severity=severity,
            status="active",
            ata_chapter=f"{random.randint(21, 80):02d}",
            flight_phase=self._flight_phase,
            flight_segment=self._current_segment,
            is_cascaded=is_cascaded,
            parent_fault_id=parent_fault_id,
            fde_code=fde_code,
            raw_data=json.dumps({"fault_code": fault_code, "severity": severity}),
        )
        db = SessionLocal()
        try:
            db.add(fault)
            db.commit()
            db.refresh(fault)
            self._total_faults += 1
        except Exception as e:
            db.rollback()
            logger.error(f"故障存储失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        # 5. WebSocket实时推送
        await ws_manager.broadcast("fault_new", {
            "id": fault.id,
            "member_system": member_system,
            "fault_code": fault.fault_code,
            "fault_text": fault.fault_text,
            "severity": severity,
            "status": "active",
            "ata_chapter": fault.ata_chapter,
            "flight_segment": self._current_segment,
            "is_cascaded": is_cascaded,
            "fde_code": fde_code,
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(f"故障已处理: {member_system} code={fault_code} severity={severity}")
        return {"status": "ok", "fault_id": fault.id}

    def get_fault_list(self, db: Session, page: int = 1, size: int = 20,
                       member: Optional[str] = None, status: Optional[str] = None) -> dict:
        """获取故障报告列表 (分页)"""
        query = db.query(FaultReport)
        if member:
            query = query.filter(FaultReport.member_system == member)
        if status:
            query = query.filter(FaultReport.status == status)
        query = query.order_by(FaultReport.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [self._fault_to_dict(f) for f in items],
        }

    def get_fault_history(self, db: Session, segment: int) -> list:
        """历史故障查询 (按航段, -128~127)"""
        items = db.query(FaultReport).filter(
            FaultReport.flight_segment == segment
        ).order_by(FaultReport.created_at.desc()).all()
        return [self._fault_to_dict(f) for f in items]

    def _fault_to_dict(self, fault: FaultReport) -> dict:
        return {
            "id": fault.id,
            "member_system": fault.member_system,
            "fault_code": fault.fault_code,
            "fault_text": fault.fault_text,
            "severity": fault.severity,
            "status": fault.status,
            "ata_chapter": fault.ata_chapter,
            "flight_phase": fault.flight_phase,
            "flight_segment": fault.flight_segment,
            "is_cascaded": fault.is_cascaded,
            "fde_code": fault.fde_code,
            "created_at": fault.created_at.isoformat() if fault.created_at else None,
            "resolved_at": fault.resolved_at.isoformat() if fault.resolved_at else None,
        }

    def resolve_fault(self, db: Session, fault_id: str) -> bool:
        """解决故障"""
        fault = db.query(FaultReport).filter(FaultReport.id == fault_id).first()
        if fault:
            fault.status = "resolved"
            fault.resolved_at = datetime.utcnow()
            db.commit()
            logger.info(f"故障已解决: {fault_id}")
            return True
        return False

    @property
    def total_faults(self) -> int:
        return self._total_faults

    def set_flight_context(self, segment: int, phase: int):
        """设置当前飞行上下文"""
        self._current_segment = segment
        self._flight_phase = phase


# 全局单例
fault_service = FaultDiagnosisService()
