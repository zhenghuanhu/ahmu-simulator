"""
Mock ARINC 硬件接口 (Windows兼容)
模拟 A664/A429/A825 板卡数据收发, 用于本地开发测试
生产环境替换为真实板卡SDK (ctypes调用.so/.dll)
"""
import asyncio
import random
import struct
import time
from typing import Callable, Optional
from loguru import logger

from app.config import SIMULATION_CONFIG


class ARINC664MockChannel:
    """
    ARINC664 (AFDX) 虚拟链路 Mock
    生产环境通过 ctypes 调用板卡SDK, 此处用模拟数据替代
    """

    def __init__(self, vl_id: int, ip: str = "127.0.0.1", port: int = 5000):
        self.vl_id = vl_id
        self.ip = ip
        self.port = port
        self._is_open = False
        self._recv_callbacks: list[Callable] = []
        self._running = False
        logger.info(f"[Mock] A664虚拟链路 VL{vl_id} 创建: {ip}:{port}")

    async def open(self):
        """打开板卡通道"""
        self._is_open = True
        logger.info(f"[Mock] A664 VL{self.vl_id} 通道已打开")

    async def close(self):
        """关闭板卡通道"""
        self._is_open = False
        self._running = False
        logger.info(f"[Mock] A664 VL{self.vl_id} 通道已关闭")

    async def send(self, data: bytes):
        """发送A664消息帧"""
        if not self._is_open:
            raise RuntimeError(f"VL{self.vl_id} 通道未打开")
        # Mock: 模拟发送延迟
        await asyncio.sleep(0.001)
        logger.debug(f"[Mock] A664 VL{self.vl_id} 发送 {len(data)} bytes")

    async def send_periodic(self, data: bytes, period_ms: int):
        """周期性发送A664消息 (1Hz等)"""
        self._running = True
        while self._running and self._is_open:
            await self.send(data)
            await asyncio.sleep(period_ms / 1000.0)

    async def receive_loop(self, callback: Callable):
        """接收循环, 回调处理接收到的数据"""
        self._running = True
        logger.info(f"[Mock] A664 VL{self.vl_id} 接收循环启动")
        while self._running and self._is_open:
            # Mock: 生成模拟数据
            mock_data = self._generate_mock_frame()
            if mock_data:
                try:
                    await callback(mock_data)
                except Exception as e:
                    logger.error(f"接收回调异常: {e}")
            await asyncio.sleep(0.01)  # 10ms间隔

    def _generate_mock_frame(self) -> Optional[bytes]:
        """生成模拟A664数据帧"""
        # 模拟成员系统故障报告/参数数据
        member_id = random.randint(1, SIMULATION_CONFIG["member_system_count"])
        frame_type = random.choice(["status", "param", "fault", "config"])

        if frame_type == "fault" and random.random() < 0.05:
            # 5%概率产生故障
            fault_code = random.randint(1, 9999)
            data = struct.pack("!BBHI", member_id, 0x01, fault_code, int(time.time()))
            return data
        elif frame_type == "status":
            # 状态数据
            status = random.choice([0x00, 0x01, 0x02, 0x03])
            data = struct.pack("!BBB", member_id, 0x02, status)
            return data
        elif frame_type == "param":
            # 参数数据
            param_val = random.uniform(0, 1000)
            data = struct.pack("!BBf", member_id, 0x03, param_val)
            return data
        return None

    def stop(self):
        """停止收发循环"""
        self._running = False


class ARINC429MockChannel:
    """ARINC429 Mock 通道"""

    def __init__(self, label: int, sdi: int = 0):
        self.label = label
        self.sdi = sdi
        self._is_open = False
        logger.info(f"[Mock] A429 Label {label:02o} SDI {sdi} 创建")

    async def open(self):
        self._is_open = True
        logger.info(f"[Mock] A429 Label {self.label:02o} 通道已打开")

    async def close(self):
        self._is_open = False

    def stop(self):
        """停止接收循环"""
        self._running = False

    async def send(self, data: int):
        """发送32位A429字"""
        if not self._is_open:
            raise RuntimeError("A429通道未打开")
        await asyncio.sleep(0.001)
        logger.debug(f"[Mock] A429 Label {self.label:02o} 发送: 0x{data:08X}")

    async def receive_loop(self, callback: Callable):
        """接收循环"""
        self._running = True
        while self._running and self._is_open:
            # 模拟A429数据
            mock_word = random.randint(0, 0x7FFFFFFF)
            try:
                await callback(self.label, mock_word)
            except Exception as e:
                logger.error(f"A429接收回调异常: {e}")
            await asyncio.sleep(0.05)


class ARINC825MockChannel:
    """ARINC825 (CAN总线航电) Mock 通道"""

    def __init__(self, can_id: int):
        self.can_id = can_id
        self._is_open = False
        logger.info(f"[Mock] A825 CAN ID 0x{can_id:X} 创建")

    async def open(self):
        self._is_open = True
        logger.info(f"[Mock] A825 CAN ID 0x{self.can_id:X} 通道已打开")

    async def close(self):
        self._is_open = False

    def stop(self):
        """停止接收循环"""
        self._running = False

    async def send(self, data: bytes):
        if not self._is_open:
            raise RuntimeError("A825通道未打开")
        await asyncio.sleep(0.001)

    async def receive_loop(self, callback: Callable):
        self._running = True
        while self._running and self._is_open:
            # 模拟CAN数据帧
            mock_data = bytes(random.randint(0, 255) for _ in range(8))
            try:
                await callback(self.can_id, mock_data)
            except Exception as e:
                logger.error(f"A825接收回调异常: {e}")
            await asyncio.sleep(0.02)


class HardwareInterface:
    """
    硬件接口统一管理器
    管理所有A664/A429/A825 Mock通道
    生产环境切换为真实板卡SDK
    """

    def __init__(self):
        self.a664_channels: dict[int, ARINC664MockChannel] = {}
        self.a429_channels: dict[int, ARINC429MockChannel] = {}
        self.a825_channels: dict[int, ARINC825MockChannel] = {}
        self._initialized = False

    async def initialize(self):
        """初始化所有硬件通道"""
        logger.info("=== 硬件接口初始化 (Mock模式) ===")

        # 创建A664虚拟链路 (模拟10条VL)
        for vl_id in range(1, 11):
            ch = ARINC664MockChannel(vl_id=vl_id, ip="127.0.0.1", port=5000 + vl_id)
            await ch.open()
            self.a664_channels[vl_id] = ch

        # 创建A429通道 (模拟20个Label)
        for label in range(1, 21):
            ch = ARINC429MockChannel(label=label, sdi=0)
            await ch.open()
            self.a429_channels[label] = ch

        # 创建A825通道 (模拟10个CAN ID)
        for can_id in range(0x100, 0x10A):
            ch = ARINC825MockChannel(can_id=can_id)
            await ch.open()
            self.a825_channels[can_id] = ch

        self._initialized = True
        logger.info(f"硬件接口初始化完成: A664={len(self.a664_channels)}ch, "
                     f"A429={len(self.a429_channels)}ch, A825={len(self.a825_channels)}ch")

    async def shutdown(self):
        """关闭所有通道"""
        for ch in self.a664_channels.values():
            ch.stop()
            await ch.close()
        for ch in self.a429_channels.values():
            ch.stop()
            await ch.close()
        for ch in self.a825_channels.values():
            ch.stop()
            await ch.close()
        logger.info("所有硬件通道已关闭")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def get_a664_channel(self, vl_id: int) -> Optional[ARINC664MockChannel]:
        return self.a664_channels.get(vl_id)

    def get_a429_channel(self, label: int) -> Optional[ARINC429MockChannel]:
        return self.a429_channels.get(label)

    def get_a825_channel(self, can_id: int) -> Optional[ARINC825MockChannel]:
        return self.a825_channels.get(can_id)


# 全局单例
hardware = HardwareInterface()
