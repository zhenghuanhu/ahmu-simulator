"""
ACARS管理服务
- ARINC619协议栈仿真
- 上下行链路管理
- 优先级调度
"""
import asyncio
import random
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, ACARSMessage
from app.core.websocket_manager import ws_manager


class ACARSService:
    """ACARS管理服务"""

    def __init__(self):
        self._running = False
        self._link_status = "idle"  # idle / busy / lost

    async def start(self):
        self._running = True
        logger.info("ACARS管理服务已启动")
        asyncio.create_task(self._link_monitor_loop())

    async def stop(self):
        self._running = False
        logger.info("ACARS管理服务已停止")

    async def send_downlink(self, message_type: str, content: str,
                            priority: int = 1) -> dict:
        """发送下行链路消息"""
        db = SessionLocal()
        try:
            msg = ACARSMessage(
                direction="downlink",
                message_type=message_type,
                priority=priority,
                content=content,
                link_status=self._link_status,
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            msg_id = msg.id
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        await ws_manager.broadcast("acars_message", {
            "id": msg_id,
            "direction": "downlink",
            "type": message_type,
            "priority": priority,
            "content": content,
            "link_status": self._link_status,
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(f"ACARS下行消息已发送: type={message_type}, priority={priority}")
        return {"status": "ok", "message_id": msg_id}

    async def receive_uplink(self, message_type: str, content: str) -> dict:
        """接收上行链路消息"""
        db = SessionLocal()
        try:
            msg = ACARSMessage(
                direction="uplink",
                message_type=message_type,
                content=content,
                link_status=self._link_status,
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            msg_id = msg.id
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        await ws_manager.broadcast("acars_message", {
            "id": msg_id,
            "direction": "uplink",
            "type": message_type,
            "content": content,
            "link_status": self._link_status,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {"status": "ok", "message_id": msg_id}

    async def _link_monitor_loop(self):
        """ACARS链路状态监控"""
        while self._running:
            try:
                # Mock: 链路状态变化
                self._link_status = random.choices(
                    ["idle", "busy", "lost"], weights=[70, 25, 5]
                )[0]

                await ws_manager.broadcast("acars_link_status", {
                    "status": self._link_status,
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                logger.error(f"ACARS链路监控异常: {e}")
            await asyncio.sleep(5.0)

    def get_messages(self, db: Session, direction: Optional[str] = None,
                     page: int = 1, size: int = 20) -> dict:
        """获取ACARS消息列表"""
        query = db.query(ACARSMessage)
        if direction:
            query = query.filter(ACARSMessage.direction == direction)
        query = query.order_by(ACARSMessage.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": m.id,
                "direction": m.direction,
                "message_type": m.message_type,
                "priority": m.priority,
                "content": m.content,
                "link_status": m.link_status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            } for m in items],
        }

    @property
    def link_status(self) -> str:
        return self._link_status


# 全局单例
acars_service = ACARSService()
