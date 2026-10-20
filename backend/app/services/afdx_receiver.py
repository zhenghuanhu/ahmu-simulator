"""
AFDX / ARINC 664 接收通道服务

监听 UDP 端口捕获 AFDX 报文, 按 ICD 消息帧定义解码为信号, 并推送至参数监控。
- 真实 AFDX 经虚拟链路(VL)承载, Mock 环境用 UDP 单播模拟 (每通道监听一个 UDP 端口)
- 解码复用 icd_afdx.decode_frame (即 MessageFrame.parse_any), 支持 SINT/UBNR/BNR/FLOAT/BOOL/DIS
- UDP 线程解码后经 asyncio.run_coroutine_threadsafe 提交主循环, 调用 param_service.ingest_afdx_signals
"""
import asyncio
import socket
import threading
from datetime import datetime
from typing import Any, Optional

from loguru import logger

from app.config import AFDX_PARAM_CONFIG
from app.core.icd_afdx import decode_frame, resolve_frame


class AFDXChannel:
    """单个 AFDX 接收通道 (UDP 端口 -> ICD 消息帧)"""

    def __init__(self, cfg: dict):
        self.name = cfg.get("name", "")
        self.member_system = cfg.get("member_system", "")
        self.frame_name = cfg.get("frame_name", "")
        self.vl_id = cfg.get("vl_id")
        self.udp_port = cfg.get("udp_port", 0)
        self.enabled = cfg.get("enabled", True)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 运行状态统计
        self.received_count = 0
        self.decoded_count = 0
        self.last_decoded: dict[str, Any] = {}
        self.last_at: Optional[str] = None
        self.last_addr: Optional[str] = None
        self.last_error: Optional[str] = None

    def start(self, host: str, callback):
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._recv_loop, args=(host, self.udp_port, callback),
            daemon=True, name=f"afdx-{self.name}")
        self._thread.start()
        logger.info(f"[AFDX] 通道已启动: {self.name} -> {host}:{self.udp_port} "
                    f"(成员 {self.member_system}, VL {self.vl_id}, 帧 {self.frame_name})")

    def stop(self):
        self._running = False
        self._thread = None

    def _recv_loop(self, host: str, port: int, callback):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as e:
            logger.error(f"[AFDX] 通道 {self.name} UDP 端口 {host}:{port} 绑定失败: {e}")
            self._running = False
            sock.close()
            return
        logger.info(f"[AFDX] 通道 {self.name} UDP 监听中: {host}:{port}")
        while self._running:
            try:
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(65535)
                self._handle(data, addr, callback)
            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"[AFDX] 通道 {self.name} 接收异常: {e}")
                self.last_error = str(e)
        sock.close()
        logger.info(f"[AFDX] 通道 {self.name} 接收线程已退出")

    def _handle(self, data: bytes, addr, callback):
        self.received_count += 1
        self.last_addr = f"{addr[0]}:{addr[1]}"
        frame = resolve_frame(self.member_system, self.frame_name, self.vl_id)
        if not frame:
            self.last_error = f"未找到 ICD 消息帧 {self.member_system}/{self.frame_name}"
            logger.warning(f"[AFDX] 通道 {self.name} {self.last_error}")
            return
        try:
            decoded = decode_frame(frame, data)
        except Exception as e:
            self.last_error = f"解码失败: {e}"
            logger.error(f"[AFDX] 通道 {self.name} {self.last_error}")
            return
        self.decoded_count += 1
        self.last_decoded = decoded
        self.last_at = datetime.now().isoformat()
        logger.debug(f"[AFDX] 通道 {self.name} 收到 {len(data)} 字节, 解码 {len(decoded)} 个信号")
        try:
            callback(self.member_system, decoded, self)
        except Exception as e:
            logger.error(f"[AFDX] 通道 {self.name} 回调异常: {e}")
            self.last_error = str(e)

    def status(self) -> dict:
        return {
            "name": self.name,
            "member_system": self.member_system,
            "frame_name": self.frame_name,
            "vl_id": self.vl_id,
            "udp_port": self.udp_port,
            "enabled": self.enabled,
            "running": self._running,
            "received_count": self.received_count,
            "decoded_count": self.decoded_count,
            "last_at": self.last_at,
            "last_addr": self.last_addr,
            "last_error": self.last_error,
        }


class AFDXReceiverService:
    """AFDX/A664 接收通道管理器 (全局单例)"""

    def __init__(self):
        self.channels = [AFDXChannel(cfg) for cfg in AFDX_PARAM_CONFIG.get("channels", [])]
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = False

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        if self._started:
            logger.warning("[AFDX] 接收通道服务已在运行")
            return
        self._loop = loop
        self._started = True
        host = AFDX_PARAM_CONFIG.get("listen_host", "0.0.0.0")
        for ch in self.channels:
            ch.start(host, self._on_receive)
        logger.info(f"[AFDX] 接收通道服务已启动: {len(self.channels)} 个通道")

    def stop(self):
        self._started = False
        for ch in self.channels:
            ch.stop()
        logger.info("[AFDX] 接收通道服务已停止")

    def _on_receive(self, member_system: str, decoded: dict, channel: AFDXChannel):
        """UDP 线程回调: 提交到主事件循环执行异步推送"""
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._push(member_system, decoded), loop)
        else:
            logger.warning(f"[AFDX] 主事件循环不可用, 丢弃 {channel.name} 解码数据")

    async def _push(self, member_system: str, decoded: dict):
        from app.services.param_monitor import param_service
        try:
            result = await param_service.ingest_afdx_signals(member_system, decoded)
            logger.debug(f"[AFDX] 参数监控更新: saved={result.get('saved')}, skipped={result.get('skipped')}")
        except Exception as e:
            logger.error(f"[AFDX] 参数监控推送异常: {e}")

    def get_status(self) -> dict:
        return {
            "started": self._started,
            "listen_host": AFDX_PARAM_CONFIG.get("listen_host", "0.0.0.0"),
            "channels": [ch.status() for ch in self.channels],
        }


# 全局单例
afdx_receiver = AFDXReceiverService()
