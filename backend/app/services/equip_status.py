"""
设备标识状态数据接收服务 (5.4.2 不同频率设备标识状态数据接收)

功能概述:
  成员系统以 0.25Hz / 0.5Hz / 1Hz / 2Hz 发送设备标识状态数据,
  基于"消息驱动的异步延迟处理算法"对这些数据进行处理。

算法四要点 (对齐 5.4.2):
  1) 消息接收与时间戳标记
     - 沿用事件驱动接收 (等价于轮询机制, 响应更及时)
     - 收到设备标识状态帧后不立即处理:
       * 在帧头附加到达时间戳 t_arrival
       * 将完整帧推入该成员对应的状态处理队列 (每个成员独立队列)
  2) 处理调度策略 (批量处理 + 最新优先)
     - 处理协程以 10Hz 频率从各队列取出数据
     - 队列 maxsize=1: 积压多条时仅保留最新一条, 丢弃中间过时数据
       (设备标识状态为周期性刷新数据, 旧状态无保留意义)
     - 处理完成后更新该成员的设备状态记录, 并更新 t_last_process
  3) 延迟约束保证 (最小周期 500ms = 2Hz)
     - 单次处理时间预算上限 100ms (远小于最小周期 500ms 的 20%)
     - 处理超时记录告警并立即返回, 等待下一个处理周期, 避免队列阻塞
     - 0.25Hz 低频数据有充足 4 秒窗口, 资源占用极低
  4) 频率无关性
     - 算法逻辑不随频率变化, 仅 period 不同
     - 低频成员队列大部分时间为空, 仅在收到消息时被短暂唤醒, CPU 占用几乎为零
"""
from __future__ import annotations
import asyncio
import json
import random
import socket
import time
from collections import deque
from datetime import datetime
from typing import Optional
from loguru import logger

from app.database import SessionLocal, EquipStatusRecord
from app.core.websocket_manager import ws_manager
from app.services.lifecycle import MEMBER_SYSTEMS, MEMBER_SYSTEM_MAP
from app.config import EQUIP_STATUS_CONFIG

# 成员系统总数 (真实 + 虚拟补足, 满足容量要求)
EQUIP_STATUS_MEMBER_COUNT = 400


class EquipStatusService:
    """设备标识状态数据接收服务 (全局单例)"""

    def __init__(self):
        cfg = EQUIP_STATUS_CONFIG
        self._running = False
        self._rx_host = cfg.get("recv_host", "0.0.0.0")
        self._rx_port = cfg.get("recv_port", 34870)
        self._report_frequencies = list(cfg.get("report_frequencies", [0.25, 0.5, 1.0, 2.0]))
        self._process_hz = float(cfg.get("process_hz", 10))
        self._timeout_budget = float(cfg.get("timeout_budget_ms", 100)) / 1000.0  # 秒

        self._member_catalog: list[dict] = []       # [{code, name, ata}]
        self._member_freq: dict[str, float] = {}    # member -> 发送频率(Hz)
        # 每成员独立状态处理队列 (deque maxlen=1 实现"最新优先", append 自动丢弃中间过时数据)
        self._queues: dict[str, deque] = {}
        # 设备状态记录 (内存最新快照) + 最后处理时间戳
        self._device_status: dict[str, dict] = {}
        self._t_last_process: dict[str, float] = {}

        self._rx_sock: Optional[socket.socket] = None

        # 统计 (可观测性)
        self._total_received = 0    # 累计接收帧数
        self._total_processed = 0   # 累计处理帧数
        self._total_dropped = 0     # 累计丢弃的过时帧数
        self._timeout_count = 0     # 累计处理超时次数

        # 测试注入点: 模拟处理耗时 (秒), 用于验证 100ms 超时预算机制 (生产默认 0)
        self._simulated_process_delay = 0.0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._build_member_catalog()
        self._setup_rx_socket()
        logger.info(
            f"设备标识状态服务已启动 (成员系统{len(self._member_catalog)}, "
            f"频率{self._report_frequencies}, 处理{self._process_hz:g}Hz, "
            f"单次预算{self._timeout_budget * 1000:.0f}ms)")
        asyncio.create_task(self._rx_loop())
        asyncio.create_task(self._process_loop())

    async def stop(self):
        self._running = False
        self._close_rx_socket()
        logger.info("设备标识状态服务已停止")

    def _build_member_catalog(self):
        """构建成员系统名录 (真实 + 虚拟补足), 并为每成员分配发送频率"""
        catalog = [{"code": code, "name": name, "ata": ata}
                   for code, name, ata in MEMBER_SYSTEMS]
        for i in range(1, EQUIP_STATUS_MEMBER_COUNT - len(catalog) + 1):
            code = f"MEM{i:03d}"
            catalog.append({"code": code, "name": f"虚拟成员系统-{i:03d}",
                            "ata": f"{(i % 60) + 1:02d}"})
        self._member_catalog = catalog
        for m in catalog:
            self._member_freq[m["code"]] = random.choice(self._report_frequencies)

    # ==================== 消息接收与时间戳标记 ====================

    def _setup_rx_socket(self):
        """创建设备标识状态数据 UDP 接收 socket (Mock AFDX)"""
        try:
            self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._rx_sock.bind((self._rx_host, self._rx_port))
            self._rx_sock.setblocking(False)
            logger.info(f"[EquipStatus] 设备标识状态 UDP 接收已监听: "
                        f"{self._rx_host}:{self._rx_port}")
        except OSError as e:
            logger.error(f"[EquipStatus] UDP socket 初始化失败: {e}")
            self._close_rx_socket()

    def _close_rx_socket(self):
        if self._rx_sock:
            try:
                self._rx_sock.close()
            except OSError:
                pass
        self._rx_sock = None

    async def _rx_loop(self):
        """UDP 接收循环: 接收设备标识状态帧 -> 附 t_arrival -> 推入成员独立队列"""
        loop = asyncio.get_event_loop()
        while self._running and self._rx_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._rx_sock, 65535)
                self._enqueue_frame(data)
            except asyncio.CancelledError:
                break
            except OSError:
                await asyncio.sleep(0.5)

    @staticmethod
    def _parse_frame(data: bytes) -> Optional[dict]:
        """解析设备标识状态帧 (Mock JSON): {member_system, equip_id, status}"""
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.warning(f"[EquipStatus] 无法解析设备标识状态帧: {data[:80]!r}")
            return None
        if not isinstance(msg, dict):
            return None
        member = str(msg.get("member_system") or "").strip()
        if not member:
            logger.warning("[EquipStatus] 设备标识状态帧缺少 member_system")
            return None
        return {
            "member_system": member,
            "equip_id": msg.get("equip_id"),
            "status": msg.get("status") if isinstance(msg.get("status"), dict) else {},
        }

    def _get_queue(self, member: str) -> deque:
        """获取/创建成员独立状态处理队列 (deque maxlen=1, 最新优先)"""
        q = self._queues.get(member)
        if q is None:
            q = deque(maxlen=1)
            self._queues[member] = q
        return q

    def _enqueue_frame(self, data: bytes):
        """收到设备标识状态帧: 附 t_arrival, 推入该成员独立队列

        最新优先: 队列 maxlen=1, 积压时先丢弃旧帧再放入新帧,
        中间过时数据无保留意义 (设备标识状态为周期性刷新数据)。
        """
        frame = self._parse_frame(data)
        if frame is None:
            return
        member = frame["member_system"]
        # 1) 帧头附加到达时间戳 t_arrival
        frame["t_arrival"] = time.monotonic()
        frame["frequency"] = self._member_freq.get(member, 1.0)
        self._total_received += 1

        q = self._get_queue(member)
        # 2) 推入独立队列 (最新优先: 队列非空则先丢弃旧帧)
        if q:
            q.popleft()
            self._total_dropped += 1
        q.append(frame)

    # ==================== 处理调度策略 ====================

    async def _process_loop(self):
        """处理调度循环: 以 10Hz 频率从各队列批量取出数据"""
        interval = 1.0 / self._process_hz
        while self._running:
            await asyncio.sleep(interval)
            try:
                await self._process_batch()
            except Exception as e:
                logger.error(f"[EquipStatus] 处理批次异常: {e}")

    async def _process_batch(self):
        """批量处理 + 最新优先: 遍历各队列, 仅取最新一条处理"""
        for member, q in list(self._queues.items()):
            if not q:
                continue
            frame = q.popleft()  # 队列 maxlen=1, 取出的即最新帧
            await self._process_one(member, frame)

    async def _process_one(self, member: str, frame: dict):
        """单成员单帧处理 (带 100ms 时间预算)

        处理超时 -> 记录告警并立即返回, 等待下一个处理周期, 避免处理队列阻塞。
        """
        try:
            await asyncio.wait_for(
                self._do_process(member, frame),
                timeout=self._timeout_budget,
            )
        except asyncio.TimeoutError:
            self._timeout_count += 1
            logger.error(
                f"[EquipStatus] 处理超时告警: 成员 {member} 单次处理超过 "
                f"{self._timeout_budget * 1000:.0f}ms 预算, 立即返回等待下周期")
            return
        # 处理完成后更新最后一次处理时间戳 t_last_process
        self._t_last_process[member] = time.monotonic()

    async def _do_process(self, member: str, frame: dict):
        """实际处理: 模拟耗时(可选) + 更新设备状态记录 + 持久化 + 推送"""
        if self._simulated_process_delay > 0:
            await asyncio.sleep(self._simulated_process_delay)
        # 1) 更新内存设备状态记录
        self._device_status[member] = frame
        self._total_processed += 1
        # 2) 持久化到数据库 (upsert)
        self._persist_status(member, frame)
        # 3) WebSocket 推送 (前端实时展示)
        await ws_manager.broadcast("equip_status", {
            "member_system": member,
            "equip_id": frame.get("equip_id"),
            "frequency": frame.get("frequency"),
            "status": frame.get("status"),
            "t_arrival": frame.get("t_arrival"),
            "t_last_process": time.monotonic(),
            "timestamp": datetime.utcnow().isoformat(),
        })

    def _persist_status(self, member: str, frame: dict):
        """持久化设备状态记录 (每成员一条最新记录, upsert)"""
        db = SessionLocal()
        try:
            rec = db.query(EquipStatusRecord).filter(
                EquipStatusRecord.member_system == member).first()
            if rec is None:
                rec = EquipStatusRecord(member_system=member)
                db.add(rec)
            rec.equip_id = frame.get("equip_id")
            rec.frequency = frame.get("frequency")
            rec.status_data = json.dumps(frame.get("status", {}), ensure_ascii=False)
            rec.t_arrival = frame.get("t_arrival")
            rec.t_last_process = time.monotonic()
            rec.processed_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[EquipStatus] 设备状态记录持久化失败: {e}")
        finally:
            db.close()

    # ==================== 查询接口 (可观测性) ====================

    def get_processing_stats(self) -> dict:
        """获取处理统计 (累计接收/处理/丢弃/超时 + 各队列积压)"""
        queued = sum(1 for q in self._queues.values() if q)
        return {
            "running": self._running,
            "process_hz": self._process_hz,
            "timeout_budget_ms": self._timeout_budget * 1000,
            "report_frequencies": self._report_frequencies,
            "member_count": len(self._member_catalog),
            "total_received": self._total_received,
            "total_processed": self._total_processed,
            "total_dropped": self._total_dropped,
            "timeout_count": self._timeout_count,
            "queued_count": queued,
            "queue_count": len(self._queues),
        }

    def get_device_status(self, member: Optional[str] = None) -> dict:
        """查询设备状态记录 (内存快照 + 数据库记录)"""
        if member:
            frame = self._device_status.get(member)
            if frame is None:
                return {"status": "ok", "member_system": member, "found": False}
            return {"status": "ok", "member_system": member, "found": True,
                    "equip_id": frame.get("equip_id"),
                    "frequency": frame.get("frequency"),
                    "status": frame.get("status"),
                    "t_last_process": self._t_last_process.get(member),
                    "member_name": MEMBER_SYSTEM_MAP.get(member, member)}
        items = []
        for code, frame in self._device_status.items():
            items.append({
                "member_system": code,
                "member_name": MEMBER_SYSTEM_MAP.get(code, code),
                "equip_id": frame.get("equip_id"),
                "frequency": frame.get("frequency"),
                "status": frame.get("status"),
                "t_last_process": self._t_last_process.get(code),
            })
        return {"status": "ok", "total": len(items), "items": items}


# 全局单例
equip_status_service = EquipStatusService()
