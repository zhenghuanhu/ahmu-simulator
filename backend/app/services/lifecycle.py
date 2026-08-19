"""
生命周期管理服务
- 维护模式校验
- 上电运行时间/上电循环计数
- 200个成员系统批量获取
"""
import asyncio
import random
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, LifecycleData
from app.core.websocket_manager import ws_manager
from app.services.maintenance_mode import maintenance_service


class LifecycleService:
    """生命周期管理服务"""

    def __init__(self):
        self._running = False
        self._retrieval_tasks: dict[str, asyncio.Task] = {}

    async def start(self):
        self._running = True
        logger.info("生命周期管理服务已启动")

    async def stop(self):
        self._running = False
        for task in self._retrieval_tasks.values():
            task.cancel()
        logger.info("生命周期管理服务已停止")

    async def retrieve_lifecycle(self, member_system: str) -> dict:
        """获取单个成员系统生命周期数据"""
        # 维护模式校验
        if maintenance_service.current_mode != "maintenance":
            return {"status": "error", "message": "非维护模式, 无法获取生命周期数据"}

        db = SessionLocal()
        try:
            existing = db.query(LifecycleData).filter(
                LifecycleData.member_system == member_system
            ).first()

            # Mock: 生成生命周期数据
            power_on_time = existing.power_on_time + random.randint(60, 3600) if existing else random.randint(100, 100000)
            power_cycle = existing.power_cycle_count + 1 if existing else random.randint(1, 500)

            if existing:
                existing.power_on_time = power_on_time
                existing.power_cycle_count = power_cycle
                existing.last_retrieved = datetime.utcnow()
                existing.retrieval_status = "success"
            else:
                ld = LifecycleData(
                    member_system=member_system,
                    power_on_time=power_on_time,
                    power_cycle_count=power_cycle,
                    last_retrieved=datetime.utcnow(),
                    retrieval_status="success",
                )
                db.add(ld)

            db.commit()

            await ws_manager.broadcast("lifecycle_retrieved", {
                "member_system": member_system,
                "power_on_time": power_on_time,
                "power_cycle_count": power_cycle,
                "timestamp": datetime.utcnow().isoformat(),
            })

            return {
                "status": "ok",
                "member_system": member_system,
                "power_on_time": power_on_time,
                "power_cycle_count": power_cycle,
            }
        except Exception as e:
            db.rollback()
            logger.error(f"生命周期数据获取失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    async def batch_retrieve(self, count: int = 200) -> dict:
        """批量获取生命周期数据 - 200个成员系统"""
        if maintenance_service.current_mode != "maintenance":
            return {"status": "error", "message": "非维护模式, 无法批量获取"}

        results = {"total": count, "success": 0, "failed": 0, "details": []}
        for i in range(1, count + 1):
            member = f"MEM{i:03d}"
            result = await self.retrieve_lifecycle(member)
            if result["status"] == "ok":
                results["success"] += 1
                results["details"].append({
                    "member_system": member,
                    "power_on_time": result["power_on_time"],
                    "power_cycle_count": result["power_cycle_count"],
                })
            else:
                results["failed"] += 1

            # 推送进度
            if i % 20 == 0:
                await ws_manager.broadcast("lifecycle_batch_progress", {
                    "total": count,
                    "completed": i,
                    "progress": round(i / count * 100, 1),
                    "timestamp": datetime.utcnow().isoformat(),
                })

            await asyncio.sleep(0.05)  # 轮询间隔

        await ws_manager.broadcast("lifecycle_batch_completed", {
            "total": count,
            "success": results["success"],
            "failed": results["failed"],
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(f"生命周期批量获取完成: {results['success']}/{results['total']}")
        return results

    def get_lifecycle_data(self, db: Session, member: Optional[str] = None,
                           page: int = 1, size: int = 20) -> dict:
        """获取生命周期数据列表"""
        query = db.query(LifecycleData)
        if member:
            query = query.filter(LifecycleData.member_system == member)
        query = query.order_by(LifecycleData.last_retrieved.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": ld.id,
                "member_system": ld.member_system,
                "power_on_time": ld.power_on_time,
                "power_cycle_count": ld.power_cycle_count,
                "last_retrieved": ld.last_retrieved.isoformat() if ld.last_retrieved else None,
                "retrieval_status": ld.retrieval_status,
            } for ld in items],
        }


# 全局单例
lifecycle_service = LifecycleService()
