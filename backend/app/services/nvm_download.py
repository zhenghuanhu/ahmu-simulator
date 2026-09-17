"""
数据下载管理服务 (4.3.14 数据下载管理)

功能概述 (依据《技术方案-20260818》4.3.14 节):
  1. 从人机界面接收成员系统 NVM 数据获取指令
  2. 仅在维护模式下允许获取并存储成员系统 NVM 数据
  3. 实时显示获取进度百分比
  4. 获取完成后将 NVM 数据及 NVM 下载日志存入本地数据库
  5. 数据库管理能力: 将数据库中存储的 NVM 数据下载到 PMAT
  6. 打印响应: 收到打印指令时, 将 NVM 数据获取结果发送至信息系统打印机

数据流:
  维护人员 (维护模式下)
        │ (获取指令, REST API)
        ▼
    AHMU 数据下载服务 ── 校验(维护模式/成员系统/类型) ── 状态机 ── 下发获取命令 ──▶ 成员系统
        ▲                                                                    │
        │ (NVM 数据, ARINC 总线返回)                                        │
        └────────────────────────────────────────────────────────────────────┘
        │ (进度推送 WebSocket / 存储 NVMData + 下载日志)
        ▼
    本地数据库 ── 下载到 PMAT ──▶ PMAT 人机界面
        │ (打印指令)
        ▼
    信息系统打印机

获取状态机:
  pending ─▶ retrieving (进度递增) ─▶ completed / failed / timeout
     │
     └─(校验失败: 非维护模式/成员系统非法/类型非法)▶ rejected
"""
import asyncio
import random
from datetime import datetime
from enum import Enum
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, NVMData, NVMDownloadLog
from app.core.websocket_manager import ws_manager
from app.config import NVM_DOWNLOAD_CONFIG, SIMULATION_CONFIG
from app.services.maintenance_mode import maintenance_service


class NVMDownloadState(str, Enum):
    """NVM 数据获取状态机"""
    PENDING = "pending"        # 已接收, 待获取
    RETRIEVING = "retrieving"  # 正在获取 (进度递增)
    COMPLETED = "completed"    # 获取完成, 数据已存储
    FAILED = "failed"          # 获取失败
    TIMEOUT = "timeout"        # 获取超时
    REJECTED = "rejected"      # 校验失败


# 进行中状态 (不允许同一成员系统并发获取)
_ACTIVE_STATES = {
    NVMDownloadState.PENDING.value,
    NVMDownloadState.RETRIEVING.value,
}


class NVMDownloadService:
    """数据下载管理服务 (全局单例)"""

    def __init__(self):
        cfg = NVM_DOWNLOAD_CONFIG
        self._running = False
        self._data_types = cfg["data_types"]
        self._retrieve_timeout = cfg.get("retrieve_timeout_sec", 15)
        self._max_concurrent = cfg.get("max_concurrent_retrieves", 5)
        self._data_size_range = cfg.get("data_size_range", [1024, 4 * 1024 * 1024])
        self._success_prob = cfg.get("retrieve_success_prob", 0.95)
        self._progress_steps = cfg.get("progress_steps", 20)
        self._member_count = SIMULATION_CONFIG.get("member_system_count", 500)

        # 进行中的获取任务: download_id -> asyncio.Task
        self._active_tasks: dict[str, asyncio.Task] = {}
        # 进行中的成员系统集合 (并发控制)
        self._active_members: set[str] = set()
        # 下载序号
        self._download_seq = 0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        logger.info("数据下载管理服务已启动")

    async def stop(self):
        self._running = False
        for task in self._active_tasks.values():
            task.cancel()
        self._active_tasks.clear()
        self._active_members.clear()
        logger.info("数据下载管理服务已停止")

    # ==================== 校验与序号 ====================

    def _generate_download_id(self) -> str:
        """生成业务下载号: DL-YYYYMMDDHHMMSS-XXXX"""
        self._download_seq += 1
        return f"DL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{self._download_seq:04d}"

    def _validate(self, member_system: str, data_type: str) -> Optional[str]:
        """校验获取请求, 返回错误信息 (None=通过)"""
        # 1. 维护模式校验 (仅维护模式下允许获取)
        if maintenance_service.current_mode != "maintenance":
            return f"非维护模式, 无法获取 NVM 数据 (当前模式: {maintenance_service.current_mode})"
        # 2. 成员系统格式校验 (MEM001 ~ MEM{count})
        if not (member_system.startswith("MEM") and member_system[3:].isdigit()):
            return f"无效的成员系统标识: {member_system}"
        num = int(member_system[3:])
        if num < 1 or num > self._member_count:
            return f"成员系统 {member_system} 超出范围 (1~{self._member_count})"
        # 3. 数据类型校验
        if data_type not in self._data_types:
            return f"无效的数据类型: {data_type} (可选: {self._data_types})"
        # 4. 并发校验
        if member_system in self._active_members:
            return f"成员系统 {member_system} 已有获取操作进行中"
        if len(self._active_members) >= self._max_concurrent:
            return f"并发获取数已达上限 ({self._max_concurrent})"
        return None

    # ==================== 获取指令接收 ====================

    async def retrieve_member_system(self, member_system: str,
                                     data_type: str = "fault_snapshot",
                                     operator: str = "TEST") -> dict:
        """从人机界面接收成员系统 NVM 数据获取指令

        入参:
          member_system: 成员系统标识 (如 MEM001)
          data_type:     NVM 数据类型 (fault_snapshot/config_snapshot/life_cycle)
          operator:      操作用户
        """
        member_system = str(member_system).strip()
        data_type = str(data_type).strip()

        err = self._validate(member_system, data_type)
        if err:
            logger.warning(f"NVM 数据获取请求被拒绝: {err}")
            return {"status": "rejected", "message": err}

        download_id = self._generate_download_id()
        log_id = self._create_log(download_id, member_system, data_type, operator)
        self._active_members.add(member_system)

        task = asyncio.create_task(
            self._execute_retrieve(log_id, download_id, member_system, data_type, operator))
        self._active_tasks[download_id] = task

        return {
            "status": "ok",
            "download_id": download_id,
            "member_system": member_system,
            "data_type": data_type,
            "message": "获取指令已下发, 正在获取 NVM 数据",
        }

    async def batch_retrieve(self, member_systems: list[str],
                             data_type: str = "fault_snapshot",
                             operator: str = "TEST") -> dict:
        """批量获取多个成员系统 NVM 数据"""
        accepted, rejected = [], []
        for member in member_systems:
            member = str(member).strip()
            err = self._validate(member, data_type)
            if err:
                rejected.append({"member_system": member, "message": err})
                continue
            download_id = self._generate_download_id()
            log_id = self._create_log(download_id, member, data_type, operator)
            self._active_members.add(member)
            task = asyncio.create_task(
                self._execute_retrieve(log_id, download_id, member, data_type, operator))
            self._active_tasks[download_id] = task
            accepted.append({"member_system": member, "download_id": download_id})

        return {
            "status": "ok",
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
        }

    # ==================== 获取状态机 ====================

    def _create_log(self, download_id: str, member_system: str,
                    data_type: str, operator: str) -> Optional[str]:
        """创建下载日志记录 (pending), 返回日志主键 id"""
        db = SessionLocal()
        log_id = None
        try:
            log = NVMDownloadLog(
                download_id=download_id,
                member_system=member_system,
                data_type=data_type,
                status=NVMDownloadState.PENDING.value,
                progress=0.0,
                operator=operator,
            )
            db.add(log)
            db.commit()
            db.refresh(log)
            log_id = log.id
        except Exception as e:
            db.rollback()
            logger.error(f"创建下载日志失败: {e}")
        finally:
            db.close()
        return log_id

    async def _execute_retrieve(self, log_id: str, download_id: str,
                                member_system: str, data_type: str, operator: str):
        """执行 NVM 数据获取状态机 (pending -> retrieving -> 终态)"""
        try:
            # 1. retrieving: 开始获取
            await self._update_log(log_id, status=NVMDownloadState.RETRIEVING.value)
            await ws_manager.broadcast("nvm_download_state", {
                "download_id": download_id,
                "member_system": member_system,
                "state": NVMDownloadState.RETRIEVING.value,
                "progress": 0.0,
                "timestamp": datetime.utcnow().isoformat(),
            })

            # 2. 进度递增 (模拟 ARINC 总线数据传输)
            data_size = random.randint(*self._data_size_range)
            for step in range(1, self._progress_steps + 1):
                if not self._running:
                    break
                progress = round(step / self._progress_steps * 100, 1)
                await self._update_log(log_id, progress=progress)
                await ws_manager.broadcast("nvm_download_progress", {
                    "download_id": download_id,
                    "member_system": member_system,
                    "progress": progress,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                await asyncio.sleep(0.1)

            # 3. 生成获取结果 (Mock)
            if random.random() < self._success_prob:
                # 存储 NVM 数据到 NVMData 表
                nvm_data_id = self._store_nvm_data(member_system, data_type, data_size)
                self._finalize_log(log_id, NVMDownloadState.COMPLETED, data_size, nvm_data_id)
                final_state = NVMDownloadState.COMPLETED
                result_message = "NVM 数据获取成功"
            else:
                self._finalize_log(log_id, NVMDownloadState.FAILED, 0, None,
                                   error="成员系统 NVM 数据获取失败")
                final_state = NVMDownloadState.FAILED
                result_message = "NVM 数据获取失败"

            await ws_manager.broadcast("nvm_download_state", {
                "download_id": download_id,
                "member_system": member_system,
                "state": final_state.value,
                "progress": 100.0 if final_state == NVMDownloadState.COMPLETED else 0.0,
                "data_size": data_size if final_state == NVMDownloadState.COMPLETED else 0,
                "result_message": result_message,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.info(
                f"NVM 数据获取完成: {download_id} {member_system} -> {final_state.value}")

        except asyncio.CancelledError:
            logger.info(f"NVM 数据获取任务被取消: {download_id}")
        except Exception as e:
            logger.error(f"NVM 数据获取执行异常: {e}")
            self._finalize_log(log_id, NVMDownloadState.FAILED, 0, None,
                               error=f"内部异常: {e}")
        finally:
            self._active_tasks.pop(download_id, None)
            self._active_members.discard(member_system)

    # ==================== 数据库操作 ====================

    def _store_nvm_data(self, member_system: str, data_type: str,
                        data_size: int) -> Optional[str]:
        """将获取到的 NVM 数据存入 NVMData 表, 返回数据主键 id"""
        db = SessionLocal()
        nvm_data_id = None
        try:
            # 模拟 NVM 数据内容 (随机二进制)
            content = bytes(random.getrandbits(8) for _ in range(min(data_size, 64 * 1024)))
            data = NVMData(
                member_system=member_system,
                data_type=data_type,
                data_content=content,
                data_size=data_size,
                download_status="stored",
            )
            db.add(data)
            db.commit()
            db.refresh(data)
            nvm_data_id = data.id
        except Exception as e:
            db.rollback()
            logger.error(f"存储 NVM 数据失败: {e}")
        finally:
            db.close()
        return nvm_data_id

    async def _update_log(self, log_id: str, status: Optional[str] = None,
                          progress: Optional[float] = None):
        db = SessionLocal()
        try:
            log = db.query(NVMDownloadLog).filter(NVMDownloadLog.id == log_id).first()
            if log:
                if status is not None:
                    log.status = status
                if progress is not None:
                    log.progress = progress
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _finalize_log(self, log_id: str, state: NVMDownloadState, data_size: int,
                      nvm_data_id: Optional[str] = None, error: Optional[str] = None):
        db = SessionLocal()
        try:
            log = db.query(NVMDownloadLog).filter(NVMDownloadLog.id == log_id).first()
            if log:
                log.status = state.value
                log.progress = 100.0 if state == NVMDownloadState.COMPLETED else log.progress
                log.data_size = data_size
                log.nvm_data_id = nvm_data_id
                log.error_message = error
                log.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    # ==================== 查询接口 ====================

    def get_download_logs(self, db: Session, member_system: Optional[str] = None,
                          status: Optional[str] = None,
                          page: int = 1, size: int = 20) -> dict:
        """查询 NVM 下载日志"""
        query = db.query(NVMDownloadLog)
        if member_system:
            query = query.filter(NVMDownloadLog.member_system == member_system)
        if status:
            query = query.filter(NVMDownloadLog.status == status)
        total = query.count()
        items = query.order_by(NVMDownloadLog.created_at.desc()) \
            .offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "download_id": l.download_id,
                "member_system": l.member_system,
                "data_type": l.data_type,
                "status": l.status,
                "progress": l.progress,
                "data_size": l.data_size,
                "operator": l.operator,
                "error_message": l.error_message,
                "started_at": l.started_at.isoformat() if l.started_at else None,
                "completed_at": l.completed_at.isoformat() if l.completed_at else None,
            } for l in items],
        }

    def get_nvm_data(self, db: Session, member_system: Optional[str] = None,
                     data_type: Optional[str] = None,
                     download_status: Optional[str] = None,
                     page: int = 1, size: int = 20) -> dict:
        """查询 NVM 数据列表 (数据库管理)"""
        query = db.query(NVMData)
        if member_system:
            query = query.filter(NVMData.member_system == member_system)
        if data_type:
            query = query.filter(NVMData.data_type == data_type)
        if download_status:
            query = query.filter(NVMData.download_status == download_status)
        total = query.count()
        items = query.order_by(NVMData.retrieved_at.desc()) \
            .offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": d.id,
                "member_system": d.member_system,
                "data_type": d.data_type,
                "data_size": d.data_size,
                "retrieved_at": d.retrieved_at.isoformat() if d.retrieved_at else None,
                "download_status": d.download_status,
            } for d in items],
        }

    # ==================== 下载到 PMAT ====================

    async def export_to_pmat(self, nvm_data_id: str) -> dict:
        """将数据库中存储的 NVM 数据下载到 PMAT (更新下载状态)"""
        db = SessionLocal()
        try:
            data = db.query(NVMData).filter(NVMData.id == nvm_data_id).first()
            if not data:
                return {"status": "error", "message": f"未找到 NVM 数据记录: {nvm_data_id}"}
            # 模拟数据传输到 PMAT
            await asyncio.sleep(0.5)
            data.download_status = "downloaded"
            db.commit()

            await ws_manager.broadcast("nvm_data_exported", {
                "nvm_data_id": nvm_data_id,
                "member_system": data.member_system,
                "data_type": data.data_type,
                "data_size": data.data_size,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.info(f"NVM 数据已下载到 PMAT: {nvm_data_id} ({data.member_system})")
            return {
                "status": "ok",
                "nvm_data_id": nvm_data_id,
                "member_system": data.member_system,
                "message": "NVM 数据已下载到 PMAT",
            }
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    # ==================== 打印 ====================

    async def print_download_result(self, download_id: str) -> dict:
        """响应打印指令, 将 NVM 数据获取结果发送至信息系统打印机"""
        from app.services.print_mgr import print_service

        db = SessionLocal()
        try:
            log = db.query(NVMDownloadLog).filter(
                NVMDownloadLog.download_id == download_id).first()
            if not log:
                return {"status": "error", "message": f"未找到下载记录: {download_id}"}

            content = (
                f"AHMU NVM 数据获取报告\n"
                f"下载号: {log.download_id}\n"
                f"成员系统: {log.member_system}\n"
                f"数据类型: {log.data_type}\n"
                f"操作用户: {log.operator}\n"
                f"操作状态: {log.status}\n"
                f"获取进度: {log.progress:.1f}%\n"
                f"数据大小: {log.data_size} 字节\n"
                f"开始时间: {log.started_at.isoformat() if log.started_at else '--'}\n"
                f"完成时间: {log.completed_at.isoformat() if log.completed_at else '--'}\n"
            )
            result = await print_service.submit_print(content, "file_transfer")
            logger.info(f"NVM 数据获取结果已提交打印: {download_id} -> 打印任务 {result.get('job_id')}")
            return {
                "status": "ok",
                "download_id": download_id,
                "print_job_id": result.get("job_id"),
                "print_dir": print_service.print_dir,
                "message": "获取结果已发送至信息系统打印机",
            }
        finally:
            db.close()

    # ==================== 状态查询 ====================

    def get_download_status(self) -> dict:
        """查询数据下载管理功能整体状态"""
        return {
            "running": self._running,
            "data_types": self._data_types,
            "retrieve_timeout_sec": self._retrieve_timeout,
            "max_concurrent": self._max_concurrent,
            "active_retrieves": list(self._active_members),
            "active_count": len(self._active_members),
            "current_mode": maintenance_service.current_mode,
        }


# 全局单例
nvm_download_service = NVMDownloadService()
