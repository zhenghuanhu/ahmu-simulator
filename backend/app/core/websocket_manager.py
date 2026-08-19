"""
WebSocket 连接管理器
支持5个终端同时接入, 实时推送故障/状态/参数数据
"""
import json
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from app.config import MAX_WS_CONNECTIONS


class ConnectionManager:
    """WebSocket连接池管理器"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> bool:
        """接受新连接, 超过最大连接数则拒绝"""
        async with self._lock:
            if len(self.active_connections) >= MAX_WS_CONNECTIONS:
                await websocket.close(code=1013, reason="Maximum connections reached")
                logger.warning(f"WebSocket连接被拒绝: 已达最大连接数 {MAX_WS_CONNECTIONS}")
                return False
            await websocket.accept()
            self.active_connections.append(websocket)
            logger.info(f"WebSocket已连接, 当前在线: {len(self.active_connections)}")
            # 发送欢迎消息
            await websocket.send_json({
                "type": "system",
                "event": "connected",
                "data": {
                    "message": "AHMU仿真器已连接",
                    "timestamp": datetime.utcnow().isoformat(),
                    "clients_online": len(self.active_connections),
                }
            })
            return True

    def disconnect(self, websocket: WebSocket):
        """移除断开连接的客户端"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket已断开, 当前在线: {len(self.active_connections)}")

    async def broadcast(self, event: str, data: dict):
        """广播消息到所有连接的客户端"""
        message = {
            "type": "broadcast",
            "event": event,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        disconnected = []
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"广播失败: {e}")
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def send_to(self, websocket: WebSocket, event: str, data: dict):
        """发送消息给指定客户端"""
        message = {
            "type": "unicast",
            "event": event,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.warning(f"单播失败: {e}")
            self.disconnect(websocket)

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


# 全局单例
ws_manager = ConnectionManager()
