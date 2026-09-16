"""
打印管理服务
- ARINC744A协议状态机仿真
- 文件传输/块传输模式
- 打印机状态监控
- 打印结果输出至电子盘 (生成 ps 格式文件, 模拟 A:\\printlog)
"""
import asyncio
import random
from datetime import datetime
from enum import Enum
from typing import Optional
from pathlib import Path
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, PrintJob
from app.core.websocket_manager import ws_manager
from app.config import PRINT_CONFIG, DATA_DIR


class PrintState(str, Enum):
    IDLE = "idle"
    RTS_SENT = "rts_sent"       # Request To Send
    WAITING_CTS = "waiting_cts"  # Waiting Clear To Send
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"


class PrintService:
    """打印管理服务"""

    def __init__(self):
        self._running = False
        self._printer_status = "ready"  # ready / busy / open / error
        # 打印输出目录 (模拟电子盘 A:\printlog)
        self._print_dir = DATA_DIR / PRINT_CONFIG.get("output_dir", "printlog")
        self._file_ext = PRINT_CONFIG.get("file_extension", "ps")
        self._simulated_drive = PRINT_CONFIG.get("simulated_drive", "A:\\printlog")
        self._print_dir.mkdir(parents=True, exist_ok=True)

    @property
    def print_dir(self) -> str:
        """模拟电子盘路径 (展示用)"""
        return self._simulated_drive

    @property
    def print_dir_real(self) -> str:
        """打印文件实际输出目录"""
        return str(self._print_dir)

    async def start(self):
        self._running = True
        logger.info("打印管理服务已启动")
        asyncio.create_task(self._printer_status_loop())

    async def stop(self):
        self._running = False
        logger.info("打印管理服务已停止")

    async def submit_print(self, content: str, job_type: str = "file_transfer") -> dict:
        """提交打印任务"""
        db = SessionLocal()
        try:
            job = PrintJob(
                job_type=job_type,
                content=content,
                status="queued",
                printer_status=self._printer_status,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        # 异步执行打印流程
        asyncio.create_task(self._execute_print(job_id, content, job_type))

        return {"status": "ok", "job_id": job_id, "message": "打印任务已提交"}

    async def _execute_print(self, job_id: str, content: str, job_type: str):
        """执行ARINC744A打印流程"""
        try:
            # 1. 发送RTS (Request To Send)
            await self._update_print_status(job_id, PrintState.RTS_SENT, "sending")
            await ws_manager.broadcast("print_state", {
                "job_id": job_id,
                "state": PrintState.RTS_SENT.value,
                "timestamp": datetime.utcnow().isoformat(),
            })
            await asyncio.sleep(0.5)

            # 2. 等待CTS (Clear To Send)
            await self._update_print_status(job_id, PrintState.WAITING_CTS, "sending")
            # 检查打印机状态: 非 ready 时等待打印机就绪 (最多 3s), 超时仍非就绪才失败
            wait_rounds = 0
            while self._printer_status != "ready" and wait_rounds < 6:
                await asyncio.sleep(0.5)
                wait_rounds += 1
            if self._printer_status != "ready":
                await self._update_print_status(job_id, PrintState.FAILED, "failed")
                await ws_manager.broadcast("print_failed", {
                    "job_id": job_id,
                    "reason": f"打印机状态: {self._printer_status}",
                    "timestamp": datetime.utcnow().isoformat(),
                })
                return

            # Mock: 模拟CTS响应
            await asyncio.sleep(0.5)

            # 3. 发送数据
            await self._update_print_status(job_id, PrintState.SENDING, "sending")

            if job_type == "file_transfer":
                # 文件传输模式: URQ -> ACK/NAK -> DATA -> ACK -> ...
                content_parts = [content[i:i+255] for i in range(0, len(content), 255)] or [content]
                for i, part in enumerate(content_parts):
                    await ws_manager.broadcast("print_progress", {
                        "job_id": job_id,
                        "progress": round((i + 1) / len(content_parts) * 100, 1),
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                    await asyncio.sleep(0.1)
            else:
                # 块传输模式: STX -> CTL1 -> CTL2 -> DATA×N -> EOT/ETX
                await asyncio.sleep(0.5)

            # 4. 完成
            # 生成 ps 格式打印文件, 输出到电子盘 (内容与打印报告一致)
            file_path = self._generate_ps_file(job_id, content)

            await self._update_print_status(job_id, PrintState.COMPLETED, "completed", file_path)
            await ws_manager.broadcast("print_completed", {
                "job_id": job_id,
                "file_path": file_path,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.info(f"打印任务完成: {job_id}, 输出文件: {file_path}")

        except Exception as e:
            logger.error(f"打印执行异常: {e}")
            await self._update_print_status(job_id, PrintState.FAILED, "failed")

    async def _update_print_status(self, job_id: str, state: PrintState, status: str,
                                   file_path: Optional[str] = None):
        db = SessionLocal()
        try:
            job = db.query(PrintJob).filter(PrintJob.id == job_id).first()
            if job:
                job.status = status
                job.printer_status = self._printer_status
                if state == PrintState.COMPLETED:
                    job.completed_at = datetime.utcnow()
                    if file_path:
                        job.file_path = file_path
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _generate_ps_file(self, job_id: str, content: str) -> str:
        """生成 ps 格式打印文件, 输出到电子盘 (模拟 A:\\printlog)

        文件内容包含打印报告文本, 与界面展示信息一致, 供 ACoreIDE 下载查看。
        """
        self._print_dir.mkdir(parents=True, exist_ok=True)
        filename = f"print_{job_id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.{self._file_ext}"
        file_path = self._print_dir / filename

        # 组装 PostScript 文件 (含报告文本内容)
        ps_lines = [
            "%!PS-Adobe-3.0",
            f"%%Title: AHMU Print Job {job_id}",
            "%%Creator: AHMU Simulator",
            f"%%CreationDate: {datetime.utcnow().isoformat()}",
            "%%Pages: 1",
            "%%EndComments",
            "",
            "/Courier findfont 11 scalefont setfont",
            "36 792 moveto",
        ]
        for line in content.split("\n"):
            # 转义 PostScript 特殊字符
            escaped = (line.replace("\\", "\\\\").replace("(", "\\(")
                       .replace(")", "\\)"))
            ps_lines.append(f"({escaped}) show")
            ps_lines.append("0 -14 rmoveto")
        ps_lines.append("showpage")
        ps_lines.append("%%EOF")

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(ps_lines))
        except Exception as e:
            logger.error(f"生成 ps 文件失败: {e}")
            return ""
        return str(file_path)

    async def _printer_status_loop(self):
        """打印机状态监控 (L350消息)"""
        while self._running:
            try:
                # Mock: 打印机状态变化
                self._printer_status = random.choices(
                    ["ready", "ready", "ready", "busy", "open", "error"],
                    weights=[50, 20, 15, 10, 3, 2]
                )[0]

                await ws_manager.broadcast("printer_status", {
                    "status": self._printer_status,
                    "sdi": "011" if self._printer_status == "open" else "000",
                    "status_code": "000" if self._printer_status == "ready" else "001",
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                logger.error(f"打印机状态监控异常: {e}")
            await asyncio.sleep(10.0)

    def get_print_jobs(self, db: Session, page: int = 1, size: int = 20) -> dict:
        query = db.query(PrintJob).order_by(PrintJob.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "print_dir": self.print_dir,
            "print_dir_real": self.print_dir_real,
            "items": [{
                "id": j.id,
                "job_type": j.job_type,
                "content": j.content[:100] if j.content else "",
                "status": j.status,
                "printer_status": j.printer_status,
                "file_path": j.file_path,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            } for j in items],
        }

    @property
    def current_printer_status(self) -> str:
        return self._printer_status


# 全局单例
print_service = PrintService()
