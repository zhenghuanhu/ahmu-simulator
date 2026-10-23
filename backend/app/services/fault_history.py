"""
故障历史检索服务 (启动测试服务 - 199 成员启动测试)

对齐 OHMS test_case/200NVM.py 的测试流程:
  对 199 个成员系统, 经 AFDX 建立 DDS 交互后执行"启动测试",
  核心是故障历史检索(FHRetrieval):
    1. FHATAsRequest   - 获取 ATA 编号列表
    2. FHEquipRequest  - 获取指定 ATA 的设备列表
    3. FHRetrievalRequest - 开始故障历史检索任务
    4. FHStatusRequest - 查询检索任务状态 (Complete/Error)

数据源: 复用 fault_diagnosis 的成员系统目录 (400 个成员, 含 code/name/ata)。
支持多达 199 个成员系统并发启动检索任务 (每任务按 equip_id 独立记录状态)。
"""
import asyncio
from datetime import datetime
from loguru import logger

from app.database import SessionLocal, FaultHistoryTask


class FaultHistoryService:
    """故障历史检索服务 (全局单例)"""

    # Mock 检索耗时 (秒): 启动后延迟此时间标记 Complete, 模拟检索过程
    RETRIEVAL_DELAY_SEC = 0.5

    def __init__(self):
        self._running = False

    async def start(self):
        self._running = True
        logger.info("故障历史检索服务已启动")
        return self

    async def stop(self):
        self._running = False
        logger.info("故障历史检索服务已停止")

    # ==================== 成员系统目录 ====================

    @property
    def _member_catalog(self) -> list:
        """复用故障诊断服务的成员系统目录 (400 个成员: code/name/ata)"""
        from app.services.fault_diagnosis import fault_service
        return getattr(fault_service, "_member_catalog", []) or []

    # ==================== 检索接口 ====================

    def get_fh_atas(self) -> dict:
        """获取 ATA 编号列表 (唯一 + 排序)"""
        ata_map = {}
        for m in self._member_catalog:
            ata = m.get("ata") or "00"
            if ata not in ata_map:
                ata_map[ata] = {"ata": ata, "ataName": f"ATA {ata}"}
        atas = sorted(ata_map.values(), key=lambda x: x["ata"])
        return {"fhATAStructList": atas, "totalCount": len(atas)}

    def get_fh_equips(self, ata: str = None) -> dict:
        """获取成员系统设备列表 (可按 ATA 过滤)"""
        items = []
        for m in self._member_catalog:
            if ata and m.get("ata") != ata:
                continue
            items.append({
                "equipID": m["code"],
                "equipName": m["name"],
                "ata": m["ata"],
            })
        return {"fhEquipStructList": items, "totalCount": len(items)}

    async def start_fh_retrieval(self, equip_id: str) -> dict:
        """启动故障历史检索任务 (Mock: 创建任务, 延迟后标记 Complete)"""
        equip_id = (equip_id or "").strip()
        if not equip_id:
            return {"status": "error", "message": "equipID 不能为空"}
        # 创建任务记录 (InProgress)
        db = SessionLocal()
        try:
            task = FaultHistoryTask(equip_id=equip_id, status="InProgress")
            db.add(task)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[FaultHistory] 创建检索任务失败: {e}")
        finally:
            db.close()
        # 后台延迟完成
        asyncio.create_task(self._complete_task(equip_id))
        logger.info(f"[FaultHistory] 已启动成员 {equip_id} 故障历史检索任务")
        return {"status": "ok", "equipID": equip_id, "taskStatus": "InProgress"}

    async def _complete_task(self, equip_id: str):
        """延迟后标记检索完成"""
        await asyncio.sleep(self.RETRIEVAL_DELAY_SEC)
        db = SessionLocal()
        try:
            # 更新该成员最近一个 InProgress 任务为 Complete
            task = db.query(FaultHistoryTask).filter(
                FaultHistoryTask.equip_id == equip_id,
                FaultHistoryTask.status == "InProgress",
            ).order_by(FaultHistoryTask.created_at.desc()).first()
            if task:
                task.status = "Complete"
                task.completed_at = datetime.utcnow()
                db.commit()
                logger.info(f"[FaultHistory] 成员 {equip_id} 故障历史检索完成")
        except Exception as e:
            db.rollback()
            logger.error(f"[FaultHistory] 更新检索状态失败: {e}")
        finally:
            db.close()

    def query_fh_status(self, equip_id: str) -> dict:
        """查询检索任务状态 (对齐 OHMS: data[0]['fhStatusStructList'][0]['status'])"""
        db = SessionLocal()
        try:
            task = db.query(FaultHistoryTask).filter(
                FaultHistoryTask.equip_id == equip_id,
            ).order_by(FaultHistoryTask.created_at.desc()).first()
            status = task.status if task else "Complete"  # 无记录视为已完成
            return {"fhStatusStructList": [{
                "status": status,
                "equipID": equip_id,
                "completedAt": task.completed_at.isoformat() if task and task.completed_at else None,
            }]}
        finally:
            db.close()

    def get_status(self) -> dict:
        """服务整体状态"""
        db = SessionLocal()
        try:
            total = db.query(FaultHistoryTask).count()
            completed = db.query(FaultHistoryTask).filter(
                FaultHistoryTask.status == "Complete").count()
        finally:
            db.close()
        return {"running": self._running, "total_tasks": total, "completed": completed}


# 全局单例
fault_history_service = FaultHistoryService()
