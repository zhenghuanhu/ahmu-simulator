"""
ACARS 管理服务 (ARINC664 数据链 — AHMU 仿真器侧)

AHMU 仿真器职责 (仅实现 AHMU 自身功能):
  1. 并行提取内部数据 (故障/失效+FDE/构型/事件+参数)
  2. 整合为结构化维护数据包 (JSON)
  3. 封装为 ARINC664 帧 (载荷=结构化维护数据包)
  4. 通过 ARINC664 总线 (Mock UDP) 向下发送
  5. 接收 CPM 经 A664 转发的上行链路请求 (EOR 标识), 触发下行
  6. 接收 CPM 的 A664 总线确认 (ACK)

ARINC619 报文的封装、664↔429 转换、CMU 终端等下游功能由 CPM 上的
ACARS 接口应用 + RDCU + CMU 完成, 在模拟程序 tests/simulate_acars_chain.py
中合并实现。

数据流方向:
  上行: CMU --A429--> RDCU --A664--> CPM --A664(EOR请求)--> AHMU
  下行: AHMU --A664(维护数据包)--> CPM --封装ARINC619+再封A664--> RDCU --A429--> CMU
       CMU/RDCU/CPM 逐级 ACK 反馈, 最终 CPM 回 A664 ACK 给 AHMU
"""
import asyncio
import json
import socket
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import (
    SessionLocal, ACARSMessage, FaultReport, FailureReport,
    FDE, MemberConfig, EventReport,
)
from app.core.websocket_manager import ws_manager
from app.core.icd_acars import (
    encode_a664_frame, decode_a664_frame,
    A664_MSG_DATA, A664_MSG_EOR, A664_MSG_ACK,
    report_type_of,
)
from app.config import ACARS_CONFIG

# 各报告类型每次下发的最大条数
REPORT_BATCH_SIZE = 20

# 报告类型 -> 中文名
REPORT_TYPE_NAMES = {
    "fault_report": "故障报告",
    "failure_report": "失效报告",
    "fde": "FDE 驾驶舱效应",
    "config_data": "构型数据",
    "event_report": "事件报告",
}


class ACARSService:
    """ACARS 管理服务 (ARINC619 数据链通信)"""

    def __init__(self):
        self._running = False
        self._link_status = "idle"  # idle / busy / lost
        self._link_updated_at: Optional[datetime] = None

        # UDP 通道 (ARINC619 over Mock UDP)
        self._recv_port = ACARS_CONFIG.get("recv_port", 34810)
        self._send_port = ACARS_CONFIG.get("send_port", 34811)
        self._recv_host = ACARS_CONFIG.get("recv_host", "0.0.0.0")
        self._ack_timeout = ACARS_CONFIG.get("ack_timeout_sec", 3)
        self._max_retry = ACARS_CONFIG.get("max_retry", 3)
        self._send_sock: Optional[socket.socket] = None
        self._recv_sock: Optional[socket.socket] = None

        # 下行配置 (可运行时修改)
        self._priority = ACARS_CONFIG.get("priority", 1)
        self._downlink_channel = ACARS_CONFIG.get("downlink_channel", "a664")
        self._trigger_mode = ACARS_CONFIG.get("trigger_mode", "on_request")

        # A664 总线确认队列 (供下行发送方等待 CPM 回 ACK)
        self._ack_queue: asyncio.Queue = asyncio.Queue()

        # 维护数据包序号
        self._seq_number = 0

        # 统计
        self._rx_count = 0
        self._tx_count = 0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._setup_udp_sockets()
        asyncio.create_task(self._recv_loop())
        asyncio.create_task(self._link_monitor_loop())
        logger.info(f"ACARS 管理服务已启动 (ARINC619, 接收UDP:{self._recv_port}, 发送UDP:{self._send_port})")

    async def stop(self):
        self._running = False
        self._close_udp_sockets()
        logger.info("ACARS 管理服务已停止")

    def _setup_udp_sockets(self):
        try:
            self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._recv_sock.bind((self._recv_host, self._recv_port))
            self._recv_sock.setblocking(False)
            logger.info(f"[ACARS] UDP 接收已监听: {self._recv_host}:{self._recv_port}")
        except OSError as e:
            logger.error(f"[ACARS] UDP socket 初始化失败: {e}")
            self._close_udp_sockets()

    def _close_udp_sockets(self):
        for sock in (self._recv_sock, self._send_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        self._recv_sock = None
        self._send_sock = None

    # ==================== UDP 收发 ====================

    async def _send_udp(self, data: bytes):
        """通过 UDP 发送 ARINC619 报文到 CPM/CMU"""
        if not self._send_sock:
            return
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(self._send_sock, data, ("127.0.0.1", self._send_port))
        self._tx_count += 1

    async def _recv_loop(self):
        """UDP 接收循环: 接收 ARINC619 上行报文并分派处理"""
        loop = asyncio.get_event_loop()
        while self._running and self._recv_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._recv_sock, 65535)
                self._rx_count += 1
                await self._handle_frame(data, addr)
            except asyncio.CancelledError:
                break
            except OSError as e:
                logger.error(f"[ACARS] UDP 接收异常: {e}")
                await asyncio.sleep(0.5)

    async def _handle_frame(self, data: bytes, addr):
        """处理接收到的 A664 帧 (上行 EOR 请求 / 下行 ACK)"""
        frame = decode_a664_frame(data)
        if not frame:
            return
        if frame["msg_type"] == A664_MSG_EOR:
            # CPM 转发的上行链路请求 (EOR 标识) -> 触发下行
            await self._handle_uplink_eor(frame)
        elif frame["msg_type"] == A664_MSG_ACK:
            # CPM 的 A664 总线确认 -> 唤醒下行发送方
            self._ack_queue.put_nowait(frame)
        else:
            logger.debug(f"[ACARS] 未识别的 A664 帧类型: {frame['msg_type']}")

    # ==================== 上行处理 ====================

    async def _handle_uplink_eor(self, frame: dict):
        """处理上行链路请求 (EOR 标识), 触发对应维护数据下行"""
        eor = frame["payload"].decode("utf-8", errors="replace").strip("\x00").strip()
        report_type = report_type_of(eor)
        await self._set_link_status("busy")
        await self._record_message("uplink", "eor", f"EOR 请求: {eor or '(空)'} -> {report_type}")
        await ws_manager.broadcast("acars_message", {
            "direction": "uplink", "type": "eor",
            "content": f"EOR 请求: {eor or '(空)'}", "link_status": self._link_status,
            "timestamp": datetime.utcnow().isoformat(),
        })
        logger.info(f"[ACARS] 收到 EOR 请求 {eor or '(空)'} -> 触发 {REPORT_TYPE_NAMES.get(report_type, report_type)} 下发")
        asyncio.create_task(self._send_downlink(report_type, eor))

    # ==================== 下行发送 ====================

    async def _send_downlink(self, report_type: str, eor: str = ""):
        """下行发送维护数据: 提取->整合->封A664->发送->等A664 ACK"""
        if report_type == "unknown" and not eor:
            return
        # 1. 并行提取内部数据 (故障/失效+FDE/构型/事件+参数)
        payload = self._collect_report_data(report_type)
        # 2. 整合为结构化维护数据包
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._seq_number += 1

        # 3. 封装 A664 帧 (载荷=结构化维护数据包)
        frame = encode_a664_frame(A664_MSG_DATA, data)

        # 4. 通过 A664 总线发送 + 等 CPM 回 A664 ACK (重发重试)
        ack = None
        for attempt in range(1, self._max_retry + 1):
            await self._send_udp(frame)
            ack = await self._wait_ack(self._ack_timeout)
            if ack:
                break
            logger.warning(f"[ACARS] 下行 {report_type} 无 A664 ACK (第 {attempt} 次重发)")
        if not ack:
            await self._mark_lost(f"下行 {report_type} 无 CPM A664 ACK (重发 {self._max_retry} 次后丢失)")
            return

        await self._set_link_status("idle")
        await self._record_message("downlink", report_type, eor or f"{REPORT_TYPE_NAMES.get(report_type, report_type)} ({len(data)} 字节)")
        await ws_manager.broadcast("acars_message", {
            "direction": "downlink",
            "type": report_type,
            "priority": self._priority,
            "content": json.dumps(payload, ensure_ascii=False),
            "link_status": self._link_status,
            "timestamp": datetime.utcnow().isoformat(),
        })
        logger.info(f"[ACARS] 下行 {report_type} 已发送: {len(data)} 字节 (EOR={eor or '手动'})")

    async def _wait_ack(self, timeout: float) -> Optional[dict]:
        """等待 CPM 的 A664 总线确认, 带超时"""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                return await asyncio.wait_for(self._ack_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None

    async def _mark_lost(self, reason: str):
        """标记链路丢失并广播"""
        self._link_status = "lost"
        self._link_updated_at = datetime.utcnow()
        await ws_manager.broadcast("acars_link_status", {
            "status": self._link_status,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        })
        logger.warning(f"[ACARS] 链路丢失: {reason}")
        # 短暂丢失后恢复空闲
        await asyncio.sleep(5.0)
        if self._link_status == "lost":
            await self._set_link_status("idle")

    async def _set_link_status(self, status: str):
        if self._link_status != status:
            self._link_status = status
            self._link_updated_at = datetime.utcnow()
            await ws_manager.broadcast("acars_link_status", {
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
            })

    # ==================== 数据源对接 ====================

    def _collect_report_data(self, report_type: str) -> dict:
        """从状态监测/故障诊断/构型报告模块获取报告数据 (经数据库共享)"""
        db = SessionLocal()
        try:
            if report_type == "fault_report":
                return self._collect_faults(db)
            if report_type == "failure_report":
                return self._collect_failures(db)
            if report_type == "fde":
                return self._collect_fde(db)
            if report_type == "config_data":
                # DNL_ACMF401: 机载系统构型数据 + 飞机状态监测事件报告 + 事件关联参数
                config = self._collect_config(db)
                events = self._collect_events(db)
                config["event_reports"] = events.get("items", [])
                config["aircraft_status"] = events.get("aircraft_status", {})
                return config
            if report_type == "event_report":
                return self._collect_events(db)
            return {"report_type": report_type, "total": 0, "items": []}
        finally:
            db.close()

    def _collect_faults(self, db: Session) -> dict:
        rows = db.query(FaultReport).order_by(FaultReport.created_at.desc()).limit(REPORT_BATCH_SIZE).all()
        return {"report_type": "fault_report", "total": len(rows), "items": [{
            "member_system": r.member_system, "fault_code": r.fault_code,
            "fault_text": r.fault_text, "severity": r.severity, "status": r.status,
            "ata_chapter": r.ata_chapter, "flight_phase": r.flight_phase,
            "flight_segment": r.flight_segment, "is_cascaded": r.is_cascaded,
            "fde_code": r.fde_code, "lru_code": r.lru_code,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]}

    def _collect_failures(self, db: Session) -> dict:
        rows = db.query(FailureReport).order_by(FailureReport.created_at.desc()).limit(REPORT_BATCH_SIZE).all()
        return {"report_type": "failure_report", "total": len(rows), "items": [{
            "member_system": r.member_system, "failure_code": r.failure_code,
            "failure_text": r.failure_text, "severity": r.severity, "status": r.status,
            "root_fault_code": r.root_fault_code, "logic_type": r.logic_type,
            "lru_code": r.lru_code, "flight_segment": r.flight_segment,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]}

    def _collect_fde(self, db: Session) -> dict:
        # 仅下发活跃故障关联的 FDE
        active_codes = {c[0] for c in db.query(FaultReport.fde_code).filter(
            FaultReport.status == "active", FaultReport.fde_code.isnot(None)).distinct().all()}
        rows = db.query(FDE).filter(FDE.fde_code.in_(active_codes)).limit(REPORT_BATCH_SIZE).all()
        if not rows:  # 无活跃 FDE 时下发全部 FDE 池
            rows = db.query(FDE).order_by(FDE.fde_code).limit(REPORT_BATCH_SIZE).all()
        return {"report_type": "fde", "total": len(rows), "items": [{
            "fde_code": r.fde_code, "fde_text": r.fde_text,
            "severity": r.severity, "ata_chapter": r.ata_chapter, "lru_code": r.lru_code,
        } for r in rows]}

    def _collect_config(self, db: Session) -> dict:
        rows = db.query(MemberConfig).order_by(MemberConfig.report_time.desc()).limit(REPORT_BATCH_SIZE).all()
        return {"report_type": "config_data", "total": len(rows), "items": [{
            "member_system": r.member_system, "config_item": r.config_item,
            "config_value": r.config_value, "config_type": r.config_type,
            "report_time": r.report_time.isoformat() if r.report_time else None,
        } for r in rows]}

    def _collect_events(self, db: Session) -> dict:
        rows = db.query(EventReport).order_by(EventReport.created_at.desc()).limit(REPORT_BATCH_SIZE).all()
        # 附加当前飞机状态监测数据 (事件关联参数)
        aircraft = {}
        try:
            from app.services.aircraft_status import aircraft_status_service
            aircraft = aircraft_status_service.get_status()
        except Exception:
            aircraft = {}
        return {
            "report_type": "event_report", "total": len(rows),
            "aircraft_status": aircraft,
            "items": [{
                "event_type": r.event_type, "member_system": r.member_system,
                "description": r.description,
                "related_fault_ids": r.related_fault_ids,
                "related_params": r.related_params,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            } for r in rows],
        }

    # ==================== 记录与查询 ====================

    async def _record_message(self, direction: str, message_type: str, content: str,
                              priority: Optional[int] = None):
        """记录 ACARS 消息 (priority 为 None 时用配置默认优先级 self._priority)"""
        db = SessionLocal()
        try:
            db.add(ACARSMessage(
                direction=direction, message_type=message_type,
                priority=self._priority if priority is None else priority,
                content=content[:500],
                link_status=self._link_status,
            ))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[ACARS] 记录消息失败: {e}")
        finally:
            db.close()

    async def send_downlink(self, message_type: str, content: str,
                            priority: Optional[int] = None) -> dict:
        """手动触发下行链路发送 (用于界面/测试)

        priority 仅作用于本条消息的优先级记录, 不改变配置的默认优先级(self._priority),
        保证配置项与消息记录中的优先级字段相互独立、不联动覆盖。
        """
        if not content:
            return {"status": "error", "message": "下发内容不能为空"}
        # 本条消息的优先级: 未指定则用配置默认值
        try:
            msg_priority = self._priority if priority is None else int(priority)
        except (TypeError, ValueError):
            msg_priority = self._priority
        # 整合为结构化维护数据包
        payload = {"report_type": message_type, "total": 1,
                   "items": [{"content": content}], "manual": True}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._seq_number += 1
        # 封装 A664 帧 (载荷=维护数据包) + 发送 + 等 CPM 回 A664 ACK
        frame = encode_a664_frame(A664_MSG_DATA, data)
        ack = None
        for attempt in range(1, self._max_retry + 1):
            await self._send_udp(frame)
            ack = await self._wait_ack(self._ack_timeout)
            if ack:
                break
        if not ack:
            await self._mark_lost(f"手动下行 {message_type} 无 CPM A664 ACK")
            return {"status": "error", "message": "无 CPM/CMU 响应 (A664 ACK 超时)"}
        await self._set_link_status("idle")
        await self._record_message("downlink", message_type, content[:500], priority=msg_priority)
        await ws_manager.broadcast("acars_message", {
            "direction": "downlink", "type": message_type,
            "priority": msg_priority, "content": content,
            "link_status": self._link_status,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return {"status": "ok", "message": "下行已发送", "priority": msg_priority}

    async def receive_uplink(self, message_type: str, content: str) -> dict:
        """记录一条上行链路消息 (兼容旧接口/测试注入)"""
        await self._record_message("uplink", message_type, content)
        await ws_manager.broadcast("acars_message", {
            "direction": "uplink", "type": message_type, "content": content,
            "link_status": self._link_status,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return {"status": "ok"}

    # ==================== 链路状态监控 ====================

    async def _link_monitor_loop(self):
        """ACARS 链路状态监控 (idle/busy/lost)"""
        while self._running:
            try:
                # 下行发送活动时保持 busy, 否则空闲; lost 由超时逻辑设置并自动恢复
                if self._link_status == "busy" and self._link_updated_at and \
                        (datetime.utcnow() - self._link_updated_at).total_seconds() > 30:
                    await self._set_link_status("idle")
            except Exception as e:
                logger.error(f"[ACARS] 链路监控异常: {e}")
            await asyncio.sleep(5.0)

    # ==================== 配置与查询 ====================

    def get_config(self) -> dict:
        return {
            "priority": self._priority,
            "downlink_channel": self._downlink_channel,
            "trigger_mode": self._trigger_mode,
            "recv_port": self._recv_port,
            "send_port": self._send_port,
            "link_status": self._link_status,
            "rx_count": self._rx_count,
            "tx_count": self._tx_count,
        }

    def set_config(self, priority: Optional[int] = None,
                   downlink_channel: Optional[str] = None,
                   trigger_mode: Optional[str] = None) -> dict:
        """设置 ACARS 传输配置 (优先级/下传链路/触发逻辑)"""
        if priority is not None:
            if priority not in (0, 1, 2):
                return {"status": "error", "message": "优先级需为 0(low)/1(normal)/2(high)"}
            self._priority = priority
        if downlink_channel is not None:
            if downlink_channel not in ("a664", "a429"):
                return {"status": "error", "message": "下传链路需为 a664/a429"}
            self._downlink_channel = downlink_channel
        if trigger_mode is not None:
            if trigger_mode not in ("on_request", "periodic", "event"):
                return {"status": "error", "message": "触发逻辑需为 on_request/periodic/event"}
            self._trigger_mode = trigger_mode
        logger.info(f"ACARS 配置已更新: priority={self._priority}, "
                    f"channel={self._downlink_channel}, trigger={self._trigger_mode}")
        return {"status": "ok", **self.get_config()}

    def get_messages(self, db: Session, direction: Optional[str] = None,
                     page: int = 1, size: int = 20) -> dict:
        """获取 ACARS 消息列表 (created_at 统一带 UTC 标记, 前端按本地时区显示)"""
        query = db.query(ACARSMessage)
        if direction:
            query = query.filter(ACARSMessage.direction == direction)
        query = query.order_by(ACARSMessage.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total, "page": page, "size": size,
            "link_status": self._link_status,
            "items": [{
                "id": m.id,
                "direction": m.direction,
                "message_type": m.message_type,
                "priority": m.priority,
                "content": m.content,
                "link_status": m.link_status,
                "created_at": (m.created_at.isoformat() + "Z") if m.created_at else None,
            } for m in items],
        }

    def delete_all_messages(self, db: Session) -> dict:
        """清除全部 ACARS 消息记录"""
        try:
            deleted = db.query(ACARSMessage).delete()
            db.commit()
            logger.info(f"已清除全部 ACARS 消息记录: {deleted} 条")
            return {"status": "ok", "deleted": deleted}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}

    @property
    def link_status(self) -> str:
        return self._link_status

    def get_link_status(self) -> dict:
        return {
            "status": self._link_status,
            "updated_at": self._link_updated_at.isoformat() if self._link_updated_at else None,
            "rx_count": self._rx_count,
            "tx_count": self._tx_count,
        }


# 全局单例
acars_service = ACARSService()
