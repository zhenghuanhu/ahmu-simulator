"""
数据重置管理服务 (4.3.10 数据重置管理)

功能概述 (依据《技术方案-20260818》4.3.10 节):
  1. 通过读取配置文件确定启用 NVM 重置服务的成员系统列表
  2. 仅能在维护模式下重置成员系统 NVM 数据
  3. 接收并存储成员系统返回的 NVM 数据重置结果
  4. 对每次重置操作记录 NVM 重置日志
  5. 支持响应用户打印指令, 将重置操作结果发送给信息系统打印机

数据流:
  维护人员 (维护模式下)
        │ (重置指令, REST API)
        ▼
    AHMU 数据重置服务 ── 校验(维护模式/成员系统启用) ── 状态机 ── 下发重置命令 ──▶ 成员系统
        ▲                                                                    │
        │ (重置结果, ARINC 总线返回)                                         │
        └────────────────────────────────────────────────────────────────────┘
        │ (存储结果 + 记录日志)
        ▼
    信息系统打印机 (打印重置操作结果) / 前端 (WebSocket 实时状态)

重置状态机:
  pending ─▶ sending ─▶ waiting_ack ─▶ success / failed / timeout
     │
     └─(校验失败: 非维护模式/成员系统未启用)▶ rejected
"""
import asyncio
import json
import os
import random
from datetime import datetime
from enum import Enum
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, NVMResetLog, NVMResetResult
from app.core.websocket_manager import ws_manager
from app.config import NVM_RESET_CONFIG, CONFIG_DIR
from app.services.maintenance_mode import maintenance_service


class ResetType(str, Enum):
    """NVM 重置类型"""
    FULL = "full"        # 全量重置
    PARTIAL = "partial"  # 部分重置


class ResetState(str, Enum):
    """NVM 重置状态机"""
    PENDING = "pending"            # 已接收, 待发送
    SENDING = "sending"            # 正在向成员系统发送重置命令
    WAITING_ACK = "waiting_ack"    # 等待成员系统返回重置结果
    SUCCESS = "success"            # 成员系统返回成功
    FAILED = "failed"              # 成员系统返回失败
    TIMEOUT = "timeout"            # 重置响应超时
    REJECTED = "rejected"          # 校验失败 (非维护模式/成员系统未启用)


# 进行中状态 (不允许同一成员系统并发重置)
_ACTIVE_STATES = {
    ResetState.PENDING.value,
    ResetState.SENDING.value,
    ResetState.WAITING_ACK.value,
}


class NVMResetService:
    """数据重置管理服务 (全局单例)"""

    def __init__(self):
        cfg = NVM_RESET_CONFIG
        self._running = False
        self._config_file = cfg["config_file"]
        self._reset_timeout = cfg.get("reset_timeout_sec", 10)
        self._reset_types = cfg.get("reset_types", ["full", "partial"])
        self._max_concurrent = cfg.get("max_concurrent_resets", 5)
        self._success_prob = cfg.get("result_success_prob", 0.92)

        # 启用 NVM 重置服务的成员系统列表 (从配置文件读取)
        self._enabled_members: list[str] = []

        # 进行中的重置任务: reset_id -> asyncio.Task
        self._active_tasks: dict[str, asyncio.Task] = {}
        # 进行中的成员系统集合 (并发控制)
        self._active_members: set[str] = set()
        # 重置序号 (用于生成业务重置号)
        self._reset_seq = 0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._load_config()
        logger.info(
            f"数据重置管理服务已启动, 启用NVM重置服务的成员系统: {len(self._enabled_members)}个")

    async def stop(self):
        self._running = False
        for task in self._active_tasks.values():
            task.cancel()
        self._active_tasks.clear()
        self._active_members.clear()
        logger.info("数据重置管理服务已停止")

    # ==================== 配置文件读取 ====================

    def _load_config(self):
        """读取配置文件, 确定启用 NVM 重置服务的成员系统列表"""
        path = os.path.join(str(CONFIG_DIR), self._config_file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            members = data.get("enabled_member_systems", [])
            # 去重 + 保序
            self._enabled_members = list(dict.fromkeys(str(m) for m in members))
            # 配置文件中的超时/类型可覆盖 config.py 默认值
            if "reset_timeout_sec" in data:
                self._reset_timeout = int(data["reset_timeout_sec"])
            if "reset_types" in data:
                self._reset_types = [str(t) for t in data["reset_types"]]
            logger.info(f"已加载 NVM 重置配置文件: {path}")
        except FileNotFoundError:
            logger.warning(f"NVM 重置配置文件不存在: {path}, 使用空列表 (无启用成员系统)")
            self._enabled_members = []
        except Exception as e:
            logger.error(f"NVM 重置配置文件解析失败: {e}")
            self._enabled_members = []

    def reload_config(self) -> dict:
        """重新加载配置文件 (便于调试/热更新)"""
        self._load_config()
        return self.get_enabled_members()

    def get_enabled_members(self) -> dict:
        """获取启用 NVM 重置服务的成员系统列表"""
        return {
            "count": len(self._enabled_members),
            "members": self._enabled_members,
            "reset_types": self._reset_types,
            "reset_timeout_sec": self._reset_timeout,
        }

    def is_member_enabled(self, member_system: str) -> bool:
        return member_system in self._enabled_members

    # ==================== 重置指令接收 ====================

    def _generate_reset_id(self) -> str:
        """生成业务重置号: NVM-YYYYMMDDHHMMSS-XXXX (含时间戳, 避免跨重启重复)"""
        self._reset_seq += 1
        return f"NVM-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{self._reset_seq:04d}"

    def _validate(self, member_system: str, reset_type: str) -> Optional[str]:
        """校验重置请求, 返回错误信息 (None=通过)"""
        # 1. 维护模式校验 (仅能在维护模式下重置成员系统 NVM 数据)
        if maintenance_service.current_mode != "maintenance":
            return f"非维护模式, 无法重置 NVM 数据 (当前模式: {maintenance_service.current_mode})"
        # 2. 成员系统启用校验
        if not self.is_member_enabled(member_system):
            return f"成员系统 {member_system} 未启用 NVM 重置服务"
        # 3. 重置类型校验
        if reset_type not in self._reset_types:
            return f"无效的重置类型: {reset_type} (可选: {self._reset_types})"
        # 4. 并发校验
        if member_system in self._active_members:
            return f"成员系统 {member_system} 已有重置操作进行中"
        if len(self._active_members) >= self._max_concurrent:
            return f"并发重置数已达上限 ({self._max_concurrent})"
        return None

    async def reset_member_system(
        self, member_system: str, reset_type: str = "full", operator: str = "TEST"
    ) -> dict:
        """发起单个成员系统 NVM 数据重置

        入参:
          member_system: 成员系统标识 (如 MEM001)
          reset_type:    重置类型 (full/partial)
          operator:      操作用户
        """
        member_system = str(member_system).strip()
        reset_type = str(reset_type).strip()

        err = self._validate(member_system, reset_type)
        if err:
            logger.warning(f"NVM 重置请求被拒绝: {err}")
            return {"status": "rejected", "message": err}

        reset_id = self._generate_reset_id()

        # 记录重置日志 (pending), 返回日志主键 id
        log_id = self._create_log(reset_id, member_system, reset_type, operator)
        self._active_members.add(member_system)

        # 异步执行重置状态机
        task = asyncio.create_task(
            self._execute_reset(log_id, reset_id, member_system, reset_type, operator))
        self._active_tasks[reset_id] = task

        return {
            "status": "ok",
            "reset_id": reset_id,
            "member_system": member_system,
            "reset_type": reset_type,
            "message": "重置指令已下发, 等待成员系统响应",
        }

    async def batch_reset(
        self, member_systems: list[str], reset_type: str = "full", operator: str = "TEST"
    ) -> dict:
        """批量重置多个成员系统 NVM 数据"""
        accepted, rejected = [], []
        for member in member_systems:
            member = str(member).strip()
            err = self._validate(member, reset_type)
            if err:
                rejected.append({"member_system": member, "message": err})
                continue
            reset_id = self._generate_reset_id()
            log_id = self._create_log(reset_id, member, reset_type, operator)
            self._active_members.add(member)
            task = asyncio.create_task(
                self._execute_reset(log_id, reset_id, member, reset_type, operator))
            self._active_tasks[reset_id] = task
            accepted.append({"member_system": member, "reset_id": reset_id})

        return {
            "status": "ok",
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
        }

    # ==================== 重置状态机 ====================

    def _create_log(self, reset_id: str, member_system: str,
                    reset_type: str, operator: str) -> Optional[str]:
        """创建重置日志记录 (pending), 返回日志主键 id (用于后续状态更新定位)"""
        db = SessionLocal()
        log_id = None
        try:
            log = NVMResetLog(
                reset_id=reset_id,
                member_system=member_system,
                reset_type=reset_type,
                status=ResetState.PENDING.value,
                operator=operator,
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            log_id = log.id
        except Exception as e:
            db.rollback()
            logger.error(f"创建重置日志失败: {e}")
        finally:
            db.close()
        return log_id

    async def _execute_reset(self, log_id: str, reset_id: str, member_system: str,
                             reset_type: str, operator: str):
        """执行 NVM 重置状态机 (pending -> sending -> waiting_ack -> 终态)"""
        try:
            # 1. sending: 通过 ARINC 总线下发重置命令
            await self._update_status(log_id, ResetState.SENDING)
            await ws_manager.broadcast("nvm_reset_state", {
                "reset_id": reset_id,
                "member_system": member_system,
                "state": ResetState.SENDING.value,
                "timestamp": datetime.utcnow().isoformat(),
            })
            await asyncio.sleep(0.5)  # 模拟 ARINC 命令发送

            # 2. waiting_ack: 等待成员系统返回结果
            await self._update_status(log_id, ResetState.WAITING_ACK)
            await ws_manager.broadcast("nvm_reset_state", {
                "reset_id": reset_id,
                "member_system": member_system,
                "state": ResetState.WAITING_ACK.value,
                "timestamp": datetime.utcnow().isoformat(),
            })

            # 模拟成员系统处理耗时 (500ms ~ 3s)
            delay = random.uniform(0.5, 3.0)
            await asyncio.sleep(min(delay, self._reset_timeout))

            # 3. 生成成员系统返回结果 (Mock)
            result = self._simulate_member_response(member_system, reset_type)

            # 3.5 故障历史重置: 清除该成员系统的故障历史记录 (FaultReport)
            if reset_type == "fault_history" and result["result_code"] == "0":
                self._clear_fault_history(member_system)

            # 4. 存储结果 + 更新日志
            self._store_result(reset_id, member_system, result)
            final_state = (ResetState.SUCCESS if result["result_code"] == "0"
                           else ResetState.FAILED)
            self._finalize_log(log_id, final_state, result)

            await ws_manager.broadcast("nvm_reset_state", {
                "reset_id": reset_id,
                "member_system": member_system,
                "state": final_state.value,
                "result_code": result["result_code"],
                "result_message": result["result_message"],
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.info(
                f"NVM 重置完成: {reset_id} {member_system} -> {final_state.value}")

        except asyncio.CancelledError:
            logger.info(f"NVM 重置任务被取消: {reset_id}")
        except Exception as e:
            logger.error(f"NVM 重置执行异常: {e}")
            self._finalize_log(log_id, ResetState.FAILED,
                               {"result_code": "-1", "result_message": f"内部异常: {e}"})
        finally:
            self._active_tasks.pop(reset_id, None)
            self._active_members.discard(member_system)

    def _simulate_member_response(self, member_system: str, reset_type: str) -> dict:
        """模拟成员系统返回的重置结果"""
        # 重置耗时 (毫秒)
        duration_ms = random.randint(120, 2800)
        # 按配置概率决定成功/失败
        if random.random() < self._success_prob:
            result_code = "0"
            # 故障历史重置与 NVM 数据重置的结果描述区分
            if reset_type == "fault_history":
                result_message = "故障历史数据重置成功"
            else:
                result_message = "NVM 数据重置成功"
        else:
            result_code = str(random.choice([1, 2, 3]))
            result_message = ("故障历史数据重置失败" if reset_type == "fault_history"
                              else "NVM 数据重置失败")
        # 模拟重置后的 NVM 校验和 (16字节十六进制)
        checksum = "".join(random.choice("0123456789abcdef") for _ in range(32))
        return {
            "result_code": result_code,
            "result_message": result_message,
            "nvm_checksum": checksum,
            "reset_duration_ms": duration_ms,
            "reset_type": reset_type,
        }

    # ==================== 数据库操作 ====================

    def _store_result(self, reset_id: str, member_system: str, result: dict):
        """存储成员系统返回的 NVM 重置结果"""
        db = SessionLocal()
        try:
            rec = NVMResetResult(
                reset_id=reset_id,
                member_system=member_system,
                result_code=result["result_code"],
                result_message=result["result_message"],
                nvm_checksum=result.get("nvm_checksum"),
                reset_duration_ms=result.get("reset_duration_ms", 0),
            )
            db.add(rec)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"存储重置结果失败: {e}")
        finally:
            db.close()

    def _clear_fault_history(self, member_system: str):
        """故障历史重置: 清除该成员系统的故障历史记录 (FaultReport)"""
        from app.database import FaultReport
        db = SessionLocal()
        try:
            cleared = db.query(FaultReport).filter(
                FaultReport.member_system == member_system).delete()
            db.commit()
            logger.info(f"故障历史重置: 已清除 {member_system} 的 {cleared} 条故障历史记录")
        except Exception as e:
            db.rollback()
            logger.error(f"清除故障历史失败: {e}")
        finally:
            db.close()

    async def _update_status(self, log_id: str, state: ResetState):
        db = SessionLocal()
        try:
            log = db.query(NVMResetLog).filter(
                NVMResetLog.id == log_id).first()
            if log:
                log.status = state.value
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _finalize_log(self, log_id: str, state: ResetState, result: dict):
        db = SessionLocal()
        try:
            log = db.query(NVMResetLog).filter(
                NVMResetLog.id == log_id).first()
            if log:
                log.status = state.value
                log.result_code = result.get("result_code")
                log.result_message = result.get("result_message")
                log.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    # ==================== 查询接口 ====================

    def get_reset_logs(self, db: Session, member_system: Optional[str] = None,
                       status: Optional[str] = None,
                       page: int = 1, size: int = 20) -> dict:
        """查询 NVM 重置日志"""
        query = db.query(NVMResetLog)
        if member_system:
            query = query.filter(NVMResetLog.member_system == member_system)
        if status:
            query = query.filter(NVMResetLog.status == status)
        total = query.count()
        items = query.order_by(NVMResetLog.created_at.desc()) \
            .offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "reset_id": l.reset_id,
                "member_system": l.member_system,
                "reset_type": l.reset_type,
                "status": l.status,
                "operator": l.operator,
                "result_code": l.result_code,
                "result_message": l.result_message,
                "error_message": l.error_message,
                "started_at": l.started_at.isoformat() if l.started_at else None,
                "completed_at": l.completed_at.isoformat() if l.completed_at else None,
            } for l in items],
        }

    def get_reset_results(self, db: Session, reset_id: Optional[str] = None,
                          member_system: Optional[str] = None,
                          page: int = 1, size: int = 20) -> dict:
        """查询 NVM 重置结果"""
        query = db.query(NVMResetResult)
        if reset_id:
            query = query.filter(NVMResetResult.reset_id == reset_id)
        if member_system:
            query = query.filter(NVMResetResult.member_system == member_system)
        total = query.count()
        items = query.order_by(NVMResetResult.received_at.desc()) \
            .offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "reset_id": r.reset_id,
                "member_system": r.member_system,
                "result_code": r.result_code,
                "result_message": r.result_message,
                "nvm_checksum": r.nvm_checksum,
                "reset_duration_ms": r.reset_duration_ms,
                "received_at": r.received_at.isoformat() if r.received_at else None,
            } for r in items],
        }

    # ==================== 打印 ====================

    async def print_reset_result(self, reset_id: str) -> dict:
        """响应打印指令, 将重置操作结果发送给信息系统打印机"""
        from app.services.print_mgr import print_service

        db = SessionLocal()
        try:
            log = db.query(NVMResetLog).filter(
                NVMResetLog.reset_id == reset_id).first()
            if not log:
                return {"status": "error", "message": f"未找到重置记录: {reset_id}"}

            # 组装打印内容 (NVM 重置操作结果报告)
            reset_type_label = ("故障历史重置" if log.reset_type == "fault_history"
                                else f"NVM 数据重置({log.reset_type})")
            content = (
                f"AHMU 数据重置操作报告\n"
                f"重置号: {log.reset_id}\n"
                f"成员系统: {log.member_system}\n"
                f"重置类型: {reset_type_label}\n"
                f"操作用户: {log.operator}\n"
                f"操作状态: {log.status}\n"
                f"结果码: {log.result_code or '--'}\n"
                f"结果描述: {log.result_message or '--'}\n"
                f"开始时间: {log.started_at.isoformat() if log.started_at else '--'}\n"
                f"完成时间: {log.completed_at.isoformat() if log.completed_at else '--'}\n"
            )
            result = await print_service.submit_print(content, "file_transfer")
            logger.info(f"NVM 重置结果已提交打印: {reset_id} -> 打印任务 {result.get('job_id')}")
            return {
                "status": "ok",
                "reset_id": reset_id,
                "print_job_id": result.get("job_id"),
                "print_dir": print_service.print_dir,
                "message": "重置结果已发送至信息系统打印机",
            }
        finally:
            db.close()

    # ==================== 状态查询 ====================

    def get_reset_status(self) -> dict:
        """查询数据重置管理功能整体状态"""
        return {
            "running": self._running,
            "enabled_member_count": len(self._enabled_members),
            "reset_types": self._reset_types,
            "reset_timeout_sec": self._reset_timeout,
            "max_concurrent": self._max_concurrent,
            "active_resets": list(self._active_members),
            "active_count": len(self._active_members),
            "current_mode": maintenance_service.current_mode,
        }


# 全局单例
nvm_reset_service = NVMResetService()
