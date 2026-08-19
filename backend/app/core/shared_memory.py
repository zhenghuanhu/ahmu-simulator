"""
共享内存接口 (Windows兼容)
与甲供系统试验软件平台通过共享内存交互
Windows使用mmap.mmap(MAP_SHARED), Linux使用/dev/shm
"""
import mmap
import os
import struct
import threading
from typing import Optional
from loguru import logger

from app.config import SHM_CONFIG


class RingBuffer:
    """环形缓冲区 - 生产者/消费者模型"""

    def __init__(self, mm: mmap.mmap, size: int, offset: int = 0):
        self.mm = mm
        self.size = size
        self.offset = offset
        self.write_pos = 0
        self.read_pos = 0
        self._lock = threading.Lock()

    def write(self, data: bytes) -> bool:
        """写入数据到环形缓冲区"""
        with self._lock:
            total_len = 8 + len(data)  # 4字节长度 + 4字节序号 + 数据
            if total_len > self.size - 8:
                return False

            pos = self.offset + self.write_pos
            # 检查是否回绕
            if pos + total_len > self.offset + self.size:
                self.write_pos = 0
                pos = self.offset

            # 写入: [长度(4B)] [序号(4B)] [数据]
            header = struct.pack("II", len(data), self.write_pos)
            self.mm[pos:pos + 8] = header
            self.mm[pos + 8:pos + 8 + len(data)] = data
            self.write_pos = (self.write_pos + total_len) % (self.size - 8)
            return True

    def read(self) -> Optional[bytes]:
        """从环形缓冲区读取数据"""
        with self._lock:
            if self.read_pos == self.write_pos:
                return None  # 无新数据

            pos = self.offset + self.read_pos
            length = struct.unpack("I", self.mm[pos:pos + 4])[0]
            if length == 0:
                return None

            seq = struct.unpack("I", self.mm[pos + 4:pos + 8])[0]
            data = bytes(self.mm[pos + 8:pos + 8 + length])
            self.read_pos = (self.read_pos + 8 + length) % (self.size - 8)
            return data


class SharedMemoryChannel:
    """
    共享内存通道 - Windows/Linux兼容
    区域划分: A664数据区 | A429数据区 | 控制命令区 | 状态反馈区
    """

    def __init__(self):
        self.name = SHM_CONFIG["name"]
        self.size = SHM_CONFIG["size"]
        self.regions = SHM_CONFIG["regions"]
        self.mm: Optional[mmap.mmap] = None
        self._initialized = False
        self.ring_buffers: dict[str, RingBuffer] = {}

        # 每个区域大小
        self.region_size = self.size // len(self.regions)

    def initialize(self):
        """初始化共享内存"""
        try:
            # Windows: 使用临时文件创建mmap
            # Linux生产环境: 使用 /dev/shm/{name}
            temp_path = os.path.join(os.environ.get("TEMP", "/tmp"), f"ahmu_shm_{os.getpid()}")
            fd = os.open(temp_path, os.O_CREAT | os.O_RDWR)
            os.ftruncate(fd, self.size)
            self.mm = mmap.mmap(fd, self.size, access=mmap.ACCESS_WRITE)

            # 初始化各区域
            region_offset = 0
            for region_name in self.regions:
                self.ring_buffers[region_name] = RingBuffer(
                    self.mm, self.region_size, region_offset
                )
                region_offset += self.region_size

            # 写入默认值
            self._write_defaults()

            self._initialized = True
            logger.info(f"共享内存初始化完成: {self.size} bytes, {len(self.regions)}个区域")
            logger.info(f"区域: {', '.join(self.regions)} (每区{self.region_size} bytes)")
        except Exception as e:
            logger.error(f"共享内存初始化失败: {e}")
            # 降级: 不使用共享内存
            self._initialized = False

    def _write_defaults(self):
        """预加载默认值"""
        for region in self.regions:
            buf = self.ring_buffers.get(region)
            if buf:
                default_data = struct.pack("I", 0) + b"\x00" * 12
                buf.write(default_data)

    def write(self, region: str, data: bytes) -> bool:
        """写入指定区域"""
        if not self._initialized:
            return False
        buf = self.ring_buffers.get(region)
        if buf is None:
            return False
        return buf.write(data)

    def read(self, region: str) -> Optional[bytes]:
        """读取指定区域"""
        if not self._initialized:
            return None
        buf = self.ring_buffers.get(region)
        if buf is None:
            return None
        return buf.read()

    def write_a664(self, data: bytes) -> bool:
        return self.write("a664", data)

    def write_a429(self, data: bytes) -> bool:
        return self.write("a429", data)

    def write_control(self, data: bytes) -> bool:
        return self.write("control", data)

    def read_status(self) -> Optional[bytes]:
        return self.read("status")

    def close(self):
        """关闭共享内存"""
        if self.mm:
            self.mm.close()
            self._initialized = False
            logger.info("共享内存已关闭")


# 全局单例
shm_channel = SharedMemoryChannel()
