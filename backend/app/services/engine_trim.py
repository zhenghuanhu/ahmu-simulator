"""
发动机配平功能服务 (4.3.9 发动机配平)

功能概述:
  1. 通过地面人机交互界面获取发动机配平指令 (source=ground)
  2. 通过驾驶舱或 PMAT 人机交互界面获取发动机配平指令 (source=cockpit/pmat)
  3. 从发动机监视装置(EMU)获取发动机配平数据
  4. 将发动机配平数据发送给驾驶舱或 PMAT 人机交互界面

数据流:
  地面HMI / 驾驶舱 / PMAT
        │ (配平指令, REST API)
        ▼
     AHMU 配平服务 ── 校验 ── 状态机 ── 下发指令 ──▶ EMU(发动机监视装置)
        ▲                                                    │
        │ (配平数据, ARINC 总线周期上报)                     │
        └────────────────────────────────────────────────────┘
        │
        ▼ (WebSocket 广播)
  驾驶舱 / PMAT 人机界面 (实时展示配平数据)

指令状态机:
  pending ─▶ validating ─▶ sending ─▶ waiting_ack ─▶ applied ─▶ completed
     │             │                              │
     │             └─(校验失败)▶ rejected         ├─(超时)▶ timeout
     │                                            └─(EMU拒绝)▶ rejected
     └────────────────────────────────────────────────────────────┘
"""
import asyncio
from datetime import datetime
from enum import Enum
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, EngineTrimCommand, EngineTrimData
from app.core.websocket_manager import ws_manager
from app.core.engine_monitor_mock import engine_monitor
from app.config import ENGINE_TRIM_CONFIG


class TrimSource(str, Enum):
    """配平指令来源"""
    GROUND = "ground"      # 地面人机交互界面
    COCKPIT = "cockpit"    # 驾驶舱
    PMAT = "pmat"          # PMAT 人机交互界面


class TrimCommandState(str, Enum):
    """配平指令状态机"""
    PENDING = "pending"            # 已接收, 待校验
    VALIDATING = "validating"      # 校验中
    SENDING = "sending"            # 指令下发 EMU 中
    WAITING_ACK = "waiting_ack"    # 等待 EMU 确认
    APPLIED = "applied"            # EMU 已执行配平
    COMPLETED = "completed"        # 配平完成
    TIMEOUT = "timeout"            # ACK 超时
    REJECTED = "rejected"          # 校验失败/EMU 拒绝


# 进行中状态 (不允许同一发动机并发新指令)
_ACTIVE_STATES = {
    TrimCommandState.PENDING.value,
    TrimCommandState.VALIDATING.value,
    TrimCommandState.SENDING.value,
    TrimCommandState.WAITING_ACK.value,
    TrimCommandState.APPLIED.value,
}


class EngineTrimService:
    """发动机配平服务 (全局单例)"""

    def __init__(self):
        cfg = ENGINE_TRIM_CONFIG
        self._running = False
        self._engine_count = cfg["engine_count"]
        self._trim_range = cfg["trim_range"]
        self._trim_types = cfg["trim_types"]
        self._command_sources = cfg["command_sources"]
        self._ack_timeout = cfg["ack_timeout_sec"]
        self._trim_apply_timeout = cfg.get("trim_apply_timeout_sec", 30)
        self._max_concurrent = cfg["max_concurrent_per_engine"]

        # 每台发动机最新配平数据 (内存缓存, 用于实时查询)
        self._latest_data: dict[int, dict] = {}
        # 进行中的指令任务: command_id -> asyncio.Task
        self._active_tasks: dict[str, asyncio.Task] = {}
        # 等待配平到位的 Future: command_id -> (engine_id, Future)
        self._apply_futures: dict[str, tuple[int, asyncio.Future]] = {}
        # 指令序号 (用于生成业务指令号)
        self._cmd_seq = 0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        # 注入 EMU 上报周期并启动数据采集
        engine_monitor.set_report_period(
            ENGINE_TRIM_CONFIG["data_report_period_ms"])
        await engine_monitor.start_monitoring(self._handle_emu_report)
        logger.info("发动机配平服务已启动")

    async def stop(self):
        self._running = False
        await engine_monitor.stop_monitoring()
        for task in self._active_tasks.values():
            task.cancel()
        self._active_tasks.clear()
        self._apply_futures.clear()
        logger.info("发动机配平服务已停止")

    # ==================== 配平指令接收 ====================

    async def submit_trim_command(self, payload: dict) -> dict:
        """接收配平指令 (地面HMI/驾驶舱/PMAT 三来源统一入口)

        入参 payload 结构:
          {
            "engine_id": int,        # 发动机编号 (1~4)
            "trim_type": str,        # thrust/power/fuel_flow
            "target_trim": float,    # 目标配平值 (%)
            "source": str,           # ground/cockpit/pmat
            "source_terminal": str,  # 来源终端标识 (可选)
            "operator": str,         # 操作员 (可选)
          }
        返回:
          {"status": "ok", "command_id": ..., "message": ...}
          {"status": "error", "message": ...}
        """
        engine_id = payload.get("engine_id")
        trim_type = payload.get("trim_type", "thrust")
        target_trim = payload.get("target_trim")
        source = payload.get("source")

        # 1. 数据校验
        valid, err = self._validate_command(engine_id, trim_type, target_trim, source)
        if not valid:
            return {"status": "error", "message": err}

        # 2. 并发限制校验 (同一发动机不允许并发配平)
        if self._has_active_command(engine_id):
            return {"status": "error",
                    "message": f"发动机 {engine_id} 已有进行中的配平指令"}

        # 3. 生成指令并持久化
        command_id = self._gen_command_id()
        db = SessionLocal()
        try:
            cmd = EngineTrimCommand(
                command_id=command_id,
                engine_id=engine_id,
                trim_type=trim_type,
                target_trim=target_trim,
                source=source,
                source_terminal=payload.get("source_terminal", ""),
                operator=payload.get("operator", "TEST"),
                status=TrimCommandState.PENDING.value,
            )
            db.add(cmd)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"创建配平指令失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        # 4. 异步执行状态机
        task = asyncio.create_task(
            self._process_command(command_id, engine_id, trim_type, target_trim, source))
        self._active_tasks[command_id] = task

        await ws_manager.broadcast("engine_trim_command_received", {
            "command_id": command_id,
            "engine_id": engine_id,
            "trim_type": trim_type,
            "target_trim": target_trim,
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {"status": "ok", "command_id": command_id,
                "message": "配平指令已接收, 正在处理"}

    # ==================== 数据校验 ====================

    def _validate_command(self, engine_id, trim_type, target_trim, source) -> tuple[bool, str]:
        """校验配平指令合法性, 返回 (是否通过, 错误信息)"""
        if not isinstance(engine_id, int) or not (1 <= engine_id <= self._engine_count):
            return False, f"发动机编号无效: {engine_id} (应为 1~{self._engine_count})"

        if trim_type not in self._trim_types:
            return False, f"配平类型无效: {trim_type} (应为 {self._trim_types})"

        if target_trim is None or not isinstance(target_trim, (int, float)):
            return False, "目标配平值缺失或类型错误"
        lo, hi = self._trim_range
        if not (lo <= float(target_trim) <= hi):
            return False, f"目标配平值越界: {target_trim} (范围 {lo}~{hi}%)"

        if source not in self._command_sources:
            return False, f"指令来源无效: {source} (应为 {self._command_sources})"

        return True, ""

    def _has_active_command(self, engine_id: int) -> bool:
        """检查指定发动机是否有进行中的配平指令"""
        db = SessionLocal()
        try:
            count = db.query(EngineTrimCommand).filter(
                EngineTrimCommand.engine_id == engine_id,
                EngineTrimCommand.status.in_(_ACTIVE_STATES),
            ).count()
            return count > 0
        except Exception as e:
            logger.error(f"检查并发配平指令失败: {e}")
            return False
        finally:
            db.close()

    def _gen_command_id(self) -> str:
        """生成业务指令号 TRIM-YYYYMMDD-HHMMSS-序号"""
        self._cmd_seq += 1
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"TRIM-{ts}-{self._cmd_seq:04d}"

    # ==================== 指令状态机 ====================

    async def _process_command(self, command_id, engine_id, trim_type,
                               target_trim, source):
        """执行配平指令状态机"""
        try:
            # validating
            await self._update_status(command_id, TrimCommandState.VALIDATING)
            await asyncio.sleep(0.2)  # 模拟校验处理

            # sending -> 通过 ARINC 总线下发指令到 EMU
            await self._update_status(command_id, TrimCommandState.SENDING)
            await asyncio.sleep(0.3)  # 模拟 ARINC 消息发送
            emu_result = engine_monitor.apply_trim(engine_id, float(target_trim))

            if not emu_result["accepted"]:
                # EMU 拒绝指令
                self._set_reject_reason(command_id, emu_result["message"])
                await self._update_status(command_id, TrimCommandState.REJECTED)
                await self._broadcast_command_state(command_id, engine_id, source,
                                                    TrimCommandState.REJECTED,
                                                    emu_result["message"])
                return

            # waiting_ack -> 收到 EMU 接受确认 (apply_trim 同步返回 accepted 即视为 ACK)
            await self._update_status(command_id, TrimCommandState.WAITING_ACK)
            self._mark_ack_received(command_id)
            await self._broadcast_command_state(command_id, engine_id, source,
                                                TrimCommandState.WAITING_ACK,
                                                "EMU 已确认接收")

            # applied -> EMU 开始执行配平
            self._mark_applied(command_id, target_trim)
            await self._update_status(command_id, TrimCommandState.APPLIED)
            await self._broadcast_command_state(command_id, engine_id, source,
                                                TrimCommandState.APPLIED,
                                                "EMU 正在执行配平")

            # 等待 EMU 配平到位 (trim_status=applied), 超时则 timeout
            await asyncio.wait_for(
                self._wait_trim_applied(command_id, engine_id),
                timeout=self._trim_apply_timeout)

            # completed
            await self._update_status(command_id, TrimCommandState.COMPLETED)
            await self._broadcast_command_state(command_id, engine_id, source,
                                                TrimCommandState.COMPLETED,
                                                "配平完成")
            logger.info(f"配平完成: {command_id} 发动机{engine_id} -> {target_trim}%")

        except asyncio.TimeoutError:
            logger.warning(f"配平执行超时: {command_id}")
            self._set_reject_reason(command_id,
                                    f"配平执行超时 (>{self._trim_apply_timeout}s)")
            await self._update_status(command_id, TrimCommandState.TIMEOUT)
            await self._broadcast_command_state(command_id, engine_id, source,
                                                TrimCommandState.TIMEOUT,
                                                "配平执行超时")
        except asyncio.CancelledError:
            logger.info(f"配平指令处理被取消: {command_id}")
        except Exception as e:
            logger.error(f"配平指令处理异常: {e}")
            await self._update_status(command_id, TrimCommandState.REJECTED)
        finally:
            self._active_tasks.pop(command_id, None)
            self._apply_futures.pop(command_id, None)

    def _wait_trim_applied(self, command_id: str, engine_id: int):
        """等待 EMU 配平到位 (由 _handle_emu_report 在 trim_status=applied 时触发)"""
        future = asyncio.get_event_loop().create_future()
        self._apply_futures[command_id] = (engine_id, future)
        return future

    async def _update_status(self, command_id: str, state: TrimCommandState):
        """更新指令状态并持久化"""
        db = SessionLocal()
        try:
            cmd = db.query(EngineTrimCommand).filter(
                EngineTrimCommand.command_id == command_id).first()
            if cmd:
                cmd.status = state.value
                if state in (TrimCommandState.COMPLETED, TrimCommandState.TIMEOUT,
                             TrimCommandState.REJECTED):
                    cmd.completed_at = datetime.utcnow()
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"更新配平指令状态失败: {e}")
        finally:
            db.close()

    def _set_reject_reason(self, command_id: str, reason: str):
        db = SessionLocal()
        try:
            cmd = db.query(EngineTrimCommand).filter(
                EngineTrimCommand.command_id == command_id).first()
            if cmd:
                cmd.reject_reason = reason
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _mark_ack_received(self, command_id: str):
        db = SessionLocal()
        try:
            cmd = db.query(EngineTrimCommand).filter(
                EngineTrimCommand.command_id == command_id).first()
            if cmd:
                cmd.ack_received = True
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _mark_applied(self, command_id: str, target_trim: float):
        db = SessionLocal()
        try:
            cmd = db.query(EngineTrimCommand).filter(
                EngineTrimCommand.command_id == command_id).first()
            if cmd:
                cmd.applied_trim = target_trim
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    async def _broadcast_command_state(self, command_id, engine_id, source,
                                       state: TrimCommandState, message: str = ""):
        await ws_manager.broadcast("engine_trim_command_state", {
            "command_id": command_id,
            "engine_id": engine_id,
            "source": source,
            "state": state.value,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # ==================== EMU 数据采集 ====================

    async def _handle_emu_report(self, engine_id: int, data: dict):
        """EMU 配平数据上报回调:
        1. 更新内存缓存
        2. 持久化快照
        3. WebSocket 推送 (发送给驾驶舱/PMAT)
        4. 若检测到配平 applied, 触发 ACK 完成
        """
        # 1. 更新内存缓存
        data["timestamp"] = datetime.utcnow().isoformat()
        self._latest_data[engine_id] = data

        # 2. 持久化快照
        db = SessionLocal()
        try:
            snap = EngineTrimData(
                engine_id=engine_id,
                n1=data.get("n1"),
                n2=data.get("n2"),
                egt=data.get("egt"),
                fuel_flow=data.get("fuel_flow"),
                thrust_rating=data.get("thrust_rating"),
                trim_value=data.get("trim_value"),
                trim_target=data.get("trim_target"),
                trim_status=data.get("trim_status"),
                validity=data.get("validity"),
                source=data.get("source", "emu"),
            )
            db.add(snap)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"存储配平数据失败: {e}")
        finally:
            db.close()

        # 3. WebSocket 推送 (配平数据 -> 驾驶舱/PMAT 界面)
        await ws_manager.broadcast("engine_trim_data", {
            "engine_id": engine_id,
            "n1": data.get("n1"),
            "n2": data.get("n2"),
            "egt": data.get("egt"),
            "fuel_flow": data.get("fuel_flow"),
            "thrust_rating": data.get("thrust_rating"),
            "trim_value": data.get("trim_value"),
            "trim_target": data.get("trim_target"),
            "trim_status": data.get("trim_status"),
            "validity": data.get("validity"),
            "timestamp": data.get("timestamp"),
        })

        # 4. 配平到位时, 触发该发动机等待中的指令完成
        if data.get("trim_status") == "applied":
            self._resolve_trim_applied(engine_id)

    def _resolve_trim_applied(self, engine_id: int):
        """配平到位(trim_status=applied)时, 触发对应指令的完成 Future"""
        for command_id, (eid, fut) in list(self._apply_futures.items()):
            if eid == engine_id and not fut.done():
                fut.set_result(True)

    # ==================== 查询接口 ====================

    def get_commands(self, db: Session, engine_id: Optional[int] = None,
                     source: Optional[str] = None,
                     page: int = 1, size: int = 20) -> dict:
        """查询配平指令列表"""
        query = db.query(EngineTrimCommand)
        if engine_id:
            query = query.filter(EngineTrimCommand.engine_id == engine_id)
        if source:
            query = query.filter(EngineTrimCommand.source == source)
        query = query.order_by(EngineTrimCommand.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total, "page": page, "size": size,
            "items": [{
                "command_id": c.command_id,
                "engine_id": c.engine_id,
                "trim_type": c.trim_type,
                "target_trim": c.target_trim,
                "source": c.source,
                "source_terminal": c.source_terminal,
                "operator": c.operator,
                "status": c.status,
                "reject_reason": c.reject_reason,
                "ack_received": c.ack_received,
                "applied_trim": c.applied_trim,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            } for c in items],
        }

    def get_latest_data(self, engine_id: Optional[int] = None) -> dict:
        """查询最新配平数据 (内存缓存, 实时)"""
        if engine_id:
            data = self._latest_data.get(engine_id)
            return {"engine_id": engine_id, "data": data or None}
        return {
            "engine_count": self._engine_count,
            "data": [self._latest_data.get(eid) for eid in
                     range(1, self._engine_count + 1)],
        }

    def get_trim_status(self) -> dict:
        """查询配平功能整体状态"""
        active_count = len(self._active_tasks)
        return {
            "running": self._running,
            "engine_count": self._engine_count,
            "trim_range": self._trim_range,
            "ack_timeout_sec": self._ack_timeout,
            "active_commands": active_count,
            "engines": [{
                "engine_id": eid,
                "data": self._latest_data.get(eid),
            } for eid in range(1, self._engine_count + 1)],
        }


# 全局单例
engine_trim_service = EngineTrimService()
