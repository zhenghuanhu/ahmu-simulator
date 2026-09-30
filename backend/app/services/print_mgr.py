"""
打印管理服务 (技术方案 4.3.2 打印管理功能)
- ARINC744A-1 协议完整仿真
- 文件传输模式: URQ -> ACK/NAK -> DATA×N(每DATA后ACK) -> 完成
- 块传输模式: RTS(含RECORD数) -> CTS -> Record×N(STX+CTL1+CTL2+DATA+EOT/ETX) + NAK重发
- 打印机状态监控 (L350消息: SDI=011 Open, Status Code=000 Ready)
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


# 协议帧类型
class FrameType(str, Enum):
    URQ = "URQ"   # User Request (用户请求)
    ACK = "ACK"   # 确认
    NAK = "NAK"   # 否定确认 (校验失败/超时/字缺失)
    RTS = "RTS"   # Request To Send (打印请求)
    CTS = "CTS"   # Clear To Send (允许打印)
    STX = "STX"   # Start of Transmission (起始符)
    CTL1 = "CTL1" # 控制字1 (行号/颜色/每行字符数/数据类型)
    CTL2 = "CTL2" # 控制字2 (字体/初始化字符位置)
    DATA = "DATA" # 数据字 (3个7位ASCII字符)
    ETX = "ETX"   # 结束文本符 (本RECORD结束, 后续RECORD即将开始)
    EOT = "EOT"   # 结束传输 (整个端口打印数据传输结束)


class PrintService:
    """打印管理服务 (ARINC744A协议仿真)"""

    # 协议参数
    RECORD_MAX_LENGTH = 255        # RECORD 最大长度(字)
    LINES_PER_RECORD = 8           # 每个 RECORD 包含的行数(用于块传输切分)
    DATA_CHARS_PER_WORD = 3        # 每个 DATA 字包含的 ASCII 字符数(7位)
    NAK_PROBABILITY = 0.05         # 模拟校验失败/超时/字缺失触发 NAK 的概率
    MAX_NAK_RETRY = 3              # 单个 RECORD 最大重发次数

    # CTL1 可选打印颜色
    PRINT_COLORS = ["black", "cyan", "red", "white"]

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
        return self._simulated_drive

    @property
    def print_dir_real(self) -> str:
        return str(self._print_dir)

    async def start(self):
        self._running = True
        logger.info("打印管理服务已启动 (ARINC744A协议仿真)")
        asyncio.create_task(self._printer_status_loop())

    async def stop(self):
        self._running = False
        logger.info("打印管理服务已停止")

    # ==================== 提交打印 ====================

    async def submit_print(self, content: str, job_type: str = "file_transfer") -> dict:
        """提交打印任务"""
        if not content:
            return {"status": "error", "message": "打印内容不能为空"}
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

        asyncio.create_task(self._execute_print(job_id, content, job_type))
        return {"status": "ok", "job_id": job_id, "message": "打印任务已提交"}

    # ==================== 协议执行 ====================

    async def _execute_print(self, job_id: str, content: str, job_type: str):
        """执行 ARINC744A 打印流程 (按模式分流)"""
        try:
            if job_type == "file_transfer":
                await self._execute_file_transfer(job_id, content)
            else:
                await self._execute_block_transfer(job_id, content)

            # 完成: 生成 ps 文件输出到电子盘
            file_path = self._generate_ps_file(job_id, content)
            await self._update_print_status(job_id, PrintState.COMPLETED, "completed", file_path)
            await ws_manager.broadcast("print_completed", {
                "job_id": job_id, "file_path": file_path,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.info(f"打印任务完成: {job_id}, 输出文件: {file_path}")
        except Exception as e:
            logger.error(f"打印执行异常: {e}")
            await self._update_print_status(job_id, PrintState.FAILED, "failed")

    async def _execute_file_transfer(self, job_id: str, content: str):
        """文件传输模式: URQ -> ACK/NAK -> DATA×N(每DATA后ACK) -> 完成"""
        await self._update_print_status(job_id, PrintState.SENDING, "sending")

        # 1. 数据链软件发出 URQ (User Request)
        await self._emit_frame(job_id, FrameType.URQ, "tx", {
            "text": "用户请求打印 (User Request)", "length": len(content)})
        await asyncio.sleep(0.4)

        # 2. 打印机响应: 未准备好发 NAK, 准备好发 ACK
        if self._printer_status != "ready":
            await self._emit_frame(job_id, FrameType.NAK, "rx", {
                "text": f"打印机未就绪 ({self._printer_status})"})
            # 等待打印机就绪 (最多 3s)
            wait = 0
            while self._printer_status != "ready" and wait < 6:
                await asyncio.sleep(0.5)
                wait += 1
            if self._printer_status != "ready":
                await self._fail(job_id, f"打印机状态: {self._printer_status}")
                return
            await self._emit_frame(job_id, FrameType.ACK, "rx", {
                "text": "打印机就绪, 可接收数据"})
        else:
            await self._emit_frame(job_id, FrameType.ACK, "rx", {
                "text": "打印机就绪, 可接收数据"})
        await asyncio.sleep(0.3)

        # 3. 数据传输: 每 DATA 后打印机 ACK
        content_parts = [content[i:i+255] for i in range(0, len(content), 255)] or [content]
        total = len(content_parts)
        for i, part in enumerate(content_parts):
            await self._emit_frame(job_id, FrameType.DATA, "tx", {
                "index": i + 1, "total": total, "length": len(part),
                "preview": part[:30],
            })
            await ws_manager.broadcast("print_progress", {
                "job_id": job_id,
                "progress": round((i + 1) / total * 100, 1),
                "timestamp": datetime.utcnow().isoformat(),
            })
            await asyncio.sleep(0.12)
            # 打印机 ACK (正常接收一个 DATA 后响应)
            await self._emit_frame(job_id, FrameType.ACK, "rx", {
                "text": f"DATA {i + 1}/{total} 接收正常"})
            await asyncio.sleep(0.08)

        # 最后 ACK 表示传输完成
        await self._emit_frame(job_id, FrameType.ACK, "rx", {
            "text": "文件传输完成"})

    async def _execute_block_transfer(self, job_id: str, content: str):
        """块传输模式: RTS(含RECORD数) -> CTS -> Record×N + NAK重发"""
        # 前置: 等待打印机就绪 (收到 L350 Ready)
        if self._printer_status != "ready":
            wait = 0
            while self._printer_status != "ready" and wait < 6:
                await asyncio.sleep(0.5)
                wait += 1
            if self._printer_status != "ready":
                await self._fail(job_id, f"打印机未就绪 ({self._printer_status})")
                return

        # 切分 Record
        records = self._split_records(content)

        # 1. RTS: 包含本次打印 RECORD 数量
        await self._update_print_status(job_id, PrintState.RTS_SENT, "sending")
        await self._emit_frame(job_id, FrameType.RTS, "tx", {
            "record_count": len(records), "text": f"打印请求 (共 {len(records)} 个 RECORD)"})
        await asyncio.sleep(0.4)

        # 2. 打印机 CTS (允许打印)
        await self._update_print_status(job_id, PrintState.WAITING_CTS, "sending")
        await self._emit_frame(job_id, FrameType.CTS, "rx", {
            "text": "允许打印 (Clear To Send)"})
        await asyncio.sleep(0.3)

        # 3. 逐 RECORD 传输
        await self._update_print_status(job_id, PrintState.SENDING, "sending")
        total = len(records)
        for idx, record in enumerate(records):
            await self._send_record(job_id, record, idx + 1, total)
            await ws_manager.broadcast("print_progress", {
                "job_id": job_id,
                "progress": round((idx + 1) / total * 100, 1),
                "timestamp": datetime.utcnow().isoformat(),
            })

    async def _send_record(self, job_id: str, record: dict, idx: int, total: int):
        """发送单个 RECORD: STX + CTL1 + CTL2 + DATA×N + EOT/ETX, 失败 NAK 重发"""
        retries = 0
        is_last = (idx == total)
        while True:
            # STX
            await self._emit_frame(job_id, FrameType.STX, "tx", {
                "record": idx, "total": total, "text": f"RECORD {idx} 起始"})
            # CTL1 (行号/颜色/每行字符数/数据类型)
            ctl1 = self._build_ctl1(record, idx)
            await self._emit_frame(job_id, FrameType.CTL1, "tx", ctl1)
            # CTL2 (字体/初始化字符位置)
            ctl2 = self._build_ctl2(record, idx)
            await self._emit_frame(job_id, FrameType.CTL2, "tx", ctl2)
            # DATA×N
            for data_word in record["data_words"]:
                await self._emit_frame(job_id, FrameType.DATA, "tx", {
                    "record": idx, "word": data_word})
                await asyncio.sleep(0.03)
            # 结束符 (最后一个 RECORD 用 EOT, 其余用 ETX)
            end_frame = FrameType.EOT if is_last else FrameType.ETX
            await self._emit_frame(job_id, end_frame, "tx", {
                "text": "整个打印数据传输结束" if is_last else "本 RECORD 结束, 后续 RECORD 即将开始"})
            await asyncio.sleep(0.2)

            # 打印机确认: 正常 ACK, 校验失败/超时/字缺失 NAK 要求重发
            if retries < self.MAX_NAK_RETRY and random.random() < self.NAK_PROBABILITY:
                retries += 1
                await self._emit_frame(job_id, FrameType.NAK, "rx", {
                    "text": f"RECORD {idx} 校验失败, 要求重发 (第 {retries} 次)"})
                await asyncio.sleep(0.2)
                continue
            await self._emit_frame(job_id, FrameType.ACK, "rx", {
                "text": f"RECORD {idx}/{total} 接收正常"})
            break

    # ==================== 协议字构建 ====================

    def _split_records(self, content: str) -> list:
        """将打印内容切分为多个 RECORD (每 RECORD 至多 LINES_PER_RECORD 行)"""
        lines = content.split("\n") if content.strip() else [content]
        records = []
        for i in range(0, len(lines), self.LINES_PER_RECORD):
            chunk = lines[i:i + self.LINES_PER_RECORD]
            text = "\n".join(chunk)
            # DATA 字: 每 3 个字符一个 DATA 字 (含 3 个 7 位 ASCII 字符)
            data_words = [text[j:j + self.DATA_CHARS_PER_WORD]
                          for j in range(0, len(text), self.DATA_CHARS_PER_WORD)]
            records.append({
                "line_start": i + 1,
                "lines": chunk,
                "text": text,
                "data_words": data_words,
            })
        return records

    def _build_ctl1(self, record: dict, idx: int) -> dict:
        """CTL1 控制字1: 行号、颜色、每行打印字符数、数据类型"""
        return {
            "line_number": record["line_start"],            # 行号
            "color": random.choice(self.PRINT_COLORS),      # 颜色
            "chars_per_line": 80,                           # 每行打印字符数
            "data_type": "alphanumeric",                    # 数据类型
            "text": "控制字1 (行号/颜色/每行字符数/数据类型)",
        }

    def _build_ctl2(self, record: dict, idx: int) -> dict:
        """CTL2 控制字2: 字体信息、初始化字符位置"""
        return {
            "font": "Courier 11pt",                         # 字体
            "char_position": 0,                             # 初始化字符位置
            "text": "控制字2 (字体/初始化字符位置)",
        }

    # ==================== 协议帧推送 ====================

    async def _emit_frame(self, job_id: str, frame_type: FrameType,
                          direction: str, detail: dict):
        """推送协议帧 (供前端展示协议交互流程)"""
        await ws_manager.broadcast("print_frame", {
            "job_id": job_id,
            "frame_type": frame_type.value,
            "direction": direction,   # tx=AHMU发送, rx=打印机响应
            "detail": detail,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def _fail(self, job_id: str, reason: str):
        await self._update_print_status(job_id, PrintState.FAILED, "failed")
        await ws_manager.broadcast("print_failed", {
            "job_id": job_id, "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # ==================== 状态与文件 ====================

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
        # 状态推送
        await ws_manager.broadcast("print_state", {
            "job_id": job_id, "state": state.value,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def _generate_ps_file(self, job_id: str, content: str) -> str:
        """生成 ps 格式打印文件, 输出到电子盘 (模拟 A:\\printlog)"""
        self._print_dir.mkdir(parents=True, exist_ok=True)
        filename = f"print_{job_id[:8]}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.{self._file_ext}"
        file_path = self._print_dir / filename

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
        """打印机状态监控 (L350消息: SDI=011 Open, Status Code=000 Ready)"""
        while self._running:
            try:
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

    # ==================== 查询 ====================

    def get_print_jobs(self, db: Session, page: int = 1, size: int = 20) -> dict:
        query = db.query(PrintJob).order_by(PrintJob.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total, "page": page, "size": size,
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
