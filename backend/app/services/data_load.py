"""
数据加载服务
- ARINC615A/665协议栈
- 串行加载, AHMU最多3个同时加载
- 300个成员系统串行加载验证
- WebSocket推送加载进度
"""
import asyncio
import random
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, DataLoadTask
from app.core.websocket_manager import ws_manager
from app.config import SIMULATION_CONFIG


class DataLoadService:
    """数据加载管理服务"""

    def __init__(self):
        self._running = False
        self._active_loads: dict[str, asyncio.Task] = {}  # task_id -> asyncio task
        self._max_concurrent = SIMULATION_CONFIG.get("max_concurrent_load", 3)

    async def start(self):
        self._running = True
        logger.info("数据加载服务已启动")

    async def stop(self):
        self._running = False
        for task in self._active_loads.values():
            task.cancel()
        self._active_loads.clear()
        logger.info("数据加载服务已停止")

    async def start_load(self, member_system: str, file_name: str = "firmware.bin") -> dict:
        """发起数据加载"""
        # 检查并发加载数限制
        if len(self._active_loads) >= self._max_concurrent:
            return {"status": "error", "message": f"已达到最大并发加载数 {self._max_concurrent}"}

        db = SessionLocal()
        try:
            task = DataLoadTask(
                member_system=member_system,
                file_name=file_name,
                file_size=random.randint(1024, 10 * 1024 * 1024),  # 1KB~10MB
                progress=0.0,
                status="loading",
                load_mode="serial",
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            task_id = task.id
        except Exception as e:
            db.rollback()
            logger.error(f"创建加载任务失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        # 异步执行加载
        coro = self._execute_load(task_id, member_system, file_name)
        self._active_loads[task_id] = asyncio.create_task(coro)

        await ws_manager.broadcast("load_started", {
            "task_id": task_id,
            "member_system": member_system,
            "file_name": file_name,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {"status": "ok", "task_id": task_id, "message": "加载已启动"}

    async def _execute_load(self, task_id: str, member_system: str, file_name: str):
        """执行数据加载流程 (Mock)"""
        total_steps = 100
        try:
            for step in range(total_steps + 1):
                if not self._running:
                    break
                progress = step / total_steps * 100

                # 更新数据库
                db = SessionLocal()
                try:
                    task = db.query(DataLoadTask).filter(DataLoadTask.id == task_id).first()
                    if task:
                        task.progress = round(progress, 1)
                        if step == total_steps:
                            task.status = "completed"
                            task.end_time = datetime.utcnow()
                        db.commit()
                except Exception as e:
                    db.rollback()
                finally:
                    db.close()

                # WebSocket推送进度
                await ws_manager.broadcast("load_progress", {
                    "task_id": task_id,
                    "member_system": member_system,
                    "file_name": file_name,
                    "progress": round(progress, 1),
                    "status": "completed" if step == total_steps else "loading",
                    "timestamp": datetime.utcnow().isoformat(),
                })

                if step < total_steps:
                    await asyncio.sleep(0.1)  # 模拟加载延迟

            logger.info(f"数据加载完成: {member_system} - {file_name}")

        except asyncio.CancelledError:
            logger.info(f"加载任务被取消: {task_id}")
            self._update_load_status(task_id, "failed", "任务被取消")
        except Exception as e:
            logger.error(f"加载执行异常: {e}")
            self._update_load_status(task_id, "failed", str(e))
        finally:
            self._active_loads.pop(task_id, None)

    def _update_load_status(self, task_id: str, status: str, message: str = ""):
        db = SessionLocal()
        try:
            task = db.query(DataLoadTask).filter(DataLoadTask.id == task_id).first()
            if task:
                task.status = status
                task.error_message = message
                task.end_time = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def get_load_tasks(self, db: Session, member: Optional[str] = None,
                       page: int = 1, size: int = 20) -> dict:
        """获取加载任务列表"""
        query = db.query(DataLoadTask)
        if member:
            query = query.filter(DataLoadTask.member_system == member)
        query = query.order_by(DataLoadTask.start_time.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": t.id,
                "member_system": t.member_system,
                "file_name": t.file_name,
                "file_size": t.file_size,
                "progress": t.progress,
                "status": t.status,
                "start_time": t.start_time.isoformat() if t.start_time else None,
                "end_time": t.end_time.isoformat() if t.end_time else None,
            } for t in items],
        }

    @property
    def active_load_count(self) -> int:
        return len(self._active_loads)


# 全局单例
data_load_service = DataLoadService()
