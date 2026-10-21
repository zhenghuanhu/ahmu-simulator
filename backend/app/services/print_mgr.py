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
import re
import socket
from datetime import datetime
from enum import Enum
from typing import Optional
from pathlib import Path
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, PrintJob
from app.core.websocket_manager import ws_manager
from app.core.icd_print import (
    encode_urq, encode_data_block, split_blocks, decode_feedback,
    encode_rts, encode_cts,
    OPT_URQ, OPT_DATA, JOB_STATUS_SUCCESS,
)
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
        self._printer_status_source = "simulated"  # 状态来源: simulated(模拟) / real(真实上报)
        self._printer_status_updated_at: Optional[datetime] = None
        # 打印输出目录 (模拟电子盘 A:\printlog)
        self._output_dir = PRINT_CONFIG.get("output_dir", "printlog")  # 可编辑的输出目录 (相对 DATA_DIR 或绝对路径)
        self._print_dir = self._resolve_print_dir(self._output_dir)
        self._file_ext = PRINT_CONFIG.get("file_extension", "ps")
        self._simulated_drive = PRINT_CONFIG.get("simulated_drive", "A:\\printlog")
        self._print_dir.mkdir(parents=True, exist_ok=True)

        # ARINC744A 打印协议通道 (Mock UDP)
        self._send_port = PRINT_CONFIG.get("send_port", 34801)
        self._recv_port = PRINT_CONFIG.get("recv_port", 34802)
        self._recv_host = PRINT_CONFIG.get("recv_host", "0.0.0.0")
        self._block_size = PRINT_CONFIG.get("block_size", 150)
        self._ack_timeout = PRINT_CONFIG.get("ack_timeout_sec", 3)
        self._max_retry = PRINT_CONFIG.get("max_retry", 3)
        # 内置模拟打印开关: True=无外部打印机时内置模拟完成; False=严格依赖打印机, 无响应判失败
        self._simulated_print_enabled = PRINT_CONFIG.get("simulated_print_enabled", True)
        self._send_sock: Optional[socket.socket] = None
        self._recv_sock: Optional[socket.socket] = None

        # 打印队列与 ACK 反馈 (串行处理, 一次一个打印任务)
        self._print_queue: asyncio.Queue = asyncio.Queue()
        self._feedback_queue: asyncio.Queue = asyncio.Queue()

    @property
    def print_dir(self) -> str:
        return self._simulated_drive

    @property
    def print_dir_real(self) -> str:
        return str(self._print_dir)

    async def start(self):
        self._running = True
        self._setup_udp_sockets()
        asyncio.create_task(self._print_worker())
        asyncio.create_task(self._recv_loop())
        asyncio.create_task(self._printer_status_loop())
        logger.info(f"打印管理服务已启动 (ARINC744A协议, 发送UDP:{self._send_port}, 接收UDP:{self._recv_port})")

    async def stop(self):
        self._running = False
        self._close_udp_sockets()
        logger.info("打印管理服务已停止")

    def _setup_udp_sockets(self):
        """创建 UDP 收发 socket (AFDX/A429 Mock 通道)"""
        try:
            self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._recv_sock.bind((self._recv_host, self._recv_port))
            self._recv_sock.setblocking(False)
            logger.info(f"[Print] UDP 接收已监听: {self._recv_host}:{self._recv_port}")
        except OSError as e:
            logger.error(f"[Print] UDP socket 初始化失败: {e}")
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

    # ==================== 提交打印 ====================

    async def submit_print(self, content: str, job_type: str = "file_transfer") -> dict:
        """提交打印任务 (进入打印队列串行处理)"""
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

        # 进入打印队列 (串行处理, 避免多任务并发 UDP 协议冲突)
        await self._print_queue.put((job_id, content, job_type))
        logger.info(f"打印任务已入队: {job_id}")
        return {"status": "ok", "job_id": job_id, "message": "打印任务已提交"}

    # ==================== 打印队列 worker ====================

    async def _print_worker(self):
        """打印队列串行 worker: 依次取出任务执行 ARINC744A 打印协议"""
        while self._running:
            try:
                job_id, content, job_type = await self._print_queue.get()
                await self._execute_print(job_id, content, job_type)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Print] 打印 worker 异常: {e}")

    # ==================== UDP 收发 ====================

    async def _recv_loop(self):
        """UDP 接收循环: 接收打印机 ACK/状态反馈, 唤醒等待中的发送方"""
        loop = asyncio.get_event_loop()
        while self._running and self._recv_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._recv_sock, 65535)
                self._handle_feedback(data, addr)
            except asyncio.CancelledError:
                break
            except OSError as e:
                logger.error(f"[Print] UDP 接收异常: {e}")
                await asyncio.sleep(0.5)

    def _handle_feedback(self, data: bytes, addr):
        """处理打印机反馈: PRINTER_STATUS 直接更新状态, 其余放入反馈队列"""
        feedback = decode_feedback(data)
        if not feedback:
            logger.warning(f"[Print] 无法解析反馈: {data[:50]!r} 来自 {addr}")
            return
        feedback["addr"] = f"{addr[0]}:{addr[1]}"

        # 真实打印机状态周期上报 (L350): 直接更新状态并标识来源 real
        if feedback.get("type") == "PRINTER_STATUS":
            code = feedback.get("code", 0)
            self._printer_status = {0: "ready", 1: "busy", 2: "open", 3: "error"}.get(code, "ready")
            self._printer_status_source = "real"
            self._printer_status_updated_at = datetime.utcnow()
            logger.debug(f"[Print] 收到真实打印机状态上报: {self._printer_status} (source=real)")
            return

        self._feedback_queue.put_nowait(feedback)
        logger.debug(f"[Print] 收到打印机反馈: {feedback.get('type')} {feedback}")

    async def _send_udp(self, data: bytes):
        """通过 UDP 发送打印数据到打印机"""
        if not self._send_sock:
            raise RuntimeError("打印发送 socket 未初始化")
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(self._send_sock, data, ("127.0.0.1", self._send_port))

    async def _wait_feedback(self, expect_type: str, timeout: float) -> Optional[dict]:
        """从反馈队列取反馈, 直到匹配期望类型 (非匹配的丢弃, 带超时)"""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                feedback = await asyncio.wait_for(self._feedback_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if feedback.get("type") == expect_type:
                return feedback
            # 非期望类型的反馈 (如 ACK 后紧跟 STATUS) 丢弃, 继续等待目标类型

    # ==================== 协议执行 ====================

    async def _execute_print(self, job_id: str, content: str, job_type: str):
        """执行 ARINC744A 打印流程 (按模式分流)"""
        try:
            success = True
            if job_type == "file_transfer":
                success = await self._execute_file_transfer(job_id, content)
            else:
                success = await self._execute_block_transfer(job_id, content)

            if not success:
                return  # 已在传输方法内部标记失败

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

    async def _send_and_wait_ack(self, job_id: str, data_msg: bytes, expect_block_number: int) -> bool:
        """通过 UDP 发送打印消息并等待对应块号的 ACK (超时重发)"""
        for attempt in range(1, self._max_retry + 1):
            await self._send_udp(data_msg)
            feedback = await self._wait_feedback("ACK", timeout=self._ack_timeout)
            if feedback and feedback.get("block_number") == expect_block_number:
                return True
            logger.warning(f"[Print] {job_id} 块 {expect_block_number} 未收到 ACK (第 {attempt} 次重发)")
        return False

    async def _execute_file_transfer(self, job_id: str, content: str) -> bool:
        """文件传输模式 (ARINC744A over AFDX/A429): URQ -> ACK -> DATA×N(每块ACK) -> Status

        通过 UDP 发送真实 URQ/DATA 分块报文, 并等待打印机 ACK/状态反馈,
        对齐 ETest A744_print_ACK.py 的接收解析逻辑。返回是否成功。
        """
        await self._update_print_status(job_id, PrintState.SENDING, "sending")

        filename = f"print_{job_id[:8]}.{self._file_ext}"
        blocks = split_blocks(content.encode("utf-8"), self._block_size)
        total = len(blocks)

        # 1. 发出 URQ (User Request: 文件名 + 总块数), 等待 ACK (单次, 无响应即降级)
        await self._emit_frame(job_id, FrameType.URQ, "tx", {
            "text": "用户请求打印 (User Request)", "filename": filename,
            "total_blocks": total, "length": len(content)})
        await self._send_udp(encode_urq(filename, total))
        urq_ack = await self._wait_feedback("ACK", timeout=self._ack_timeout)
        if not (urq_ack and urq_ack.get("block_number") == 0):
            if self._simulated_print_enabled:
                # 内置模拟打印开启: 无外部打印机时降级为内置模拟完成
                await self._emit_frame(job_id, FrameType.ACK, "rx", {
                    "text": "无外部打印机响应, 内置模拟打印完成"})
                logger.warning(f"[Print] {job_id} 无外部打印机响应, 内置模拟完成")
                return True
            await self._fail(job_id, "无外部打印机响应 (内置模拟打印已禁用)")
            return False

        # 2. 逐块发送 DATA, 每块等待 ACK (BlockNumber)
        for i, block in enumerate(blocks, start=1):
            await self._emit_frame(job_id, FrameType.DATA, "tx", {
                "index": i, "total": total, "length": len(block),
                "preview": block[:30].decode("utf-8", errors="replace")})
            await ws_manager.broadcast("print_progress", {
                "job_id": job_id,
                "progress": round(i / total * 100, 1),
                "timestamp": datetime.utcnow().isoformat(),
            })
            if not await self._send_and_wait_ack(job_id, encode_data_block(i, block), i):
                if self._simulated_print_enabled:
                    # 打印机中途消失: 内置模拟打印开启则降级为内置模拟完成
                    logger.warning(f"[Print] {job_id} 数据块 {i}/{total} 无 ACK, 内置模拟完成")
                    return True
                await self._fail(job_id, f"数据块 {i}/{total} 无 ACK (内置模拟打印已禁用)")
                return False

        # 3. 等待作业状态反馈 (JobStatus=成功)
        await self._emit_frame(job_id, FrameType.EOT, "tx", {
            "text": "整个打印数据传输结束, 等待作业状态反馈"})
        status = await self._wait_feedback("STATUS", timeout=self._ack_timeout)
        if not (status and status.get("job_status") == JOB_STATUS_SUCCESS):
            await self._fail(job_id, f"作业状态反馈失败或非成功: {status}")
            return False
        return True

    async def _execute_block_transfer(self, job_id: str, content: str) -> bool:
        """块传输模式 (ARINC744A over UDP): RTS -> CTS -> DATA×N(每块ACK) -> Status

        通过 UDP 发送 RTS 握手与 DATA 分块, 等待打印机 CTS/ACK/状态反馈。
        返回是否成功 (无打印机时降级为内置模拟完成)。
        """
        await self._update_print_status(job_id, PrintState.RTS_SENT, "sending")

        blocks = split_blocks(content.encode("utf-8"), self._block_size)
        total = len(blocks)

        # 1. RTS (含 RECORD 数量), 等 CTS
        await self._emit_frame(job_id, FrameType.RTS, "tx", {
            "record_count": total, "text": f"打印请求 (共 {total} 个 RECORD)"})
        await self._send_udp(encode_rts(total))
        cts = await self._wait_feedback("CTS", timeout=self._ack_timeout)
        if not cts:
            if self._simulated_print_enabled:
                await self._emit_frame(job_id, FrameType.CTS, "rx", {
                    "text": "无外部打印机响应, 内置模拟打印完成"})
                logger.warning(f"[Print] {job_id} 无外部打印机 CTS 响应, 内置模拟完成")
                return True
            await self._fail(job_id, "无外部打印机 CTS 响应 (内置模拟打印已禁用)")
            return False
        await self._emit_frame(job_id, FrameType.CTS, "rx", {
            "text": "允许打印 (Clear To Send)"})

        # 2. 逐块发送 DATA, 每块等待 ACK
        await self._update_print_status(job_id, PrintState.SENDING, "sending")
        for i, block in enumerate(blocks, start=1):
            await self._emit_frame(job_id, FrameType.DATA, "tx", {
                "index": i, "total": total, "length": len(block),
                "preview": block[:30].decode("utf-8", errors="replace")})
            await ws_manager.broadcast("print_progress", {
                "job_id": job_id,
                "progress": round(i / total * 100, 1),
                "timestamp": datetime.utcnow().isoformat(),
            })
            if not await self._send_and_wait_ack(job_id, encode_data_block(i, block), i):
                if self._simulated_print_enabled:
                    logger.warning(f"[Print] {job_id} 数据块 {i}/{total} 无 ACK, 内置模拟完成")
                    return True
                await self._fail(job_id, f"数据块 {i}/{total} 无 ACK (内置模拟打印已禁用)")
                return False

        # 3. 等待作业状态反馈
        await self._emit_frame(job_id, FrameType.EOT, "tx", {
            "text": "整个打印数据传输结束, 等待作业状态反馈"})
        status = await self._wait_feedback("STATUS", timeout=self._ack_timeout)
        if not (status and status.get("job_status") == JOB_STATUS_SUCCESS):
            await self._fail(job_id, f"作业状态反馈失败或非成功: {status}")
            return False
        return True

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
        """打印机状态监控 (L350消息): 真实上报未过期时保持, 否则按开关决定是否模拟生成"""
        while self._running:
            try:
                # 有真实上报且未过期 (15s 内) 则保持真实
                if (self._printer_status_source == "real" and self._printer_status_updated_at
                        and (datetime.utcnow() - self._printer_status_updated_at).total_seconds() < 15):
                    pass  # 保持真实状态
                elif self._simulated_print_enabled:
                    # 内置模拟打印开启: 无真实上报时随机模拟打印机状态
                    self._printer_status = random.choices(
                        ["ready", "ready", "ready", "busy", "open", "error"],
                        weights=[50, 20, 15, 10, 3, 2]
                    )[0]
                    self._printer_status_source = "simulated"
                    self._printer_status_updated_at = datetime.utcnow()
                else:
                    # 内置模拟打印禁用: 不随机模拟, 无真实上报时固定为 ready 并标识 simulated
                    self._printer_status = "ready"
                    self._printer_status_source = "simulated"
                    self._printer_status_updated_at = datetime.utcnow()
                await ws_manager.broadcast("printer_status", {
                    "status": self._printer_status,
                    "source": self._printer_status_source,
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

    def get_printer_status(self) -> dict:
        """查询打印机状态 (含来源标识: real=真实上报 / simulated=模拟生成)"""
        return {
            "status": self._printer_status,
            "source": self._printer_status_source,
            "updated_at": self._printer_status_updated_at.isoformat()
            if self._printer_status_updated_at else None,
        }

    # ==================== 打印配置 ====================

    @staticmethod
    def _resolve_print_dir(dir_name: str) -> Path:
        """解析打印输出目录: 绝对路径原样使用, 相对路径拼到 DATA_DIR 下"""
        name = (dir_name or "").strip()
        p = Path(name)
        return p if p.is_absolute() else DATA_DIR / name

    def _set_output_dir(self, dir_name: str) -> dict:
        """设置打印输出目录 (校验 + 建目录 + 更新)"""
        name = (dir_name or "").strip()
        if not name:
            return {"status": "error", "message": "打印输出目录不能为空"}
        # 禁止根目录/盘符根 (防止打印文件散落到系统根目录): 去尾斜杠后为空, 或形如 "C:" 的盘符根
        stripped = name.rstrip("\\/")
        if stripped == "" or re.fullmatch(r"[A-Za-z]:", stripped):
            return {"status": "error", "message": "打印输出目录不能为根目录或盘符根"}
        try:
            new_dir = self._resolve_print_dir(name)
            new_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"status": "error", "message": f"目录创建失败: {e}"}
        self._output_dir = name
        self._print_dir = new_dir
        logger.info(f"打印输出目录已更新: {new_dir}")
        return {"status": "ok", "output_dir": name, "print_dir_real": str(new_dir)}

    def set_simulated_print(self, enabled: bool) -> dict:
        """设置内置模拟打印开关 (True=无打印机时内置模拟完成, False=严格依赖打印机)"""
        self._simulated_print_enabled = bool(enabled)
        logger.info(f"打印配置: 内置模拟打印 {'启用' if self._simulated_print_enabled else '禁用'}")
        return {"status": "ok", "simulated_print_enabled": self._simulated_print_enabled}

    def set_print_config(self, simulated_print_enabled: Optional[bool] = None,
                         output_dir: Optional[str] = None) -> dict:
        """设置打印配置 (内置模拟打印开关 + 打印输出目录), 返回完整配置"""
        if simulated_print_enabled is not None:
            self._simulated_print_enabled = bool(simulated_print_enabled)
        if output_dir is not None:
            res = self._set_output_dir(output_dir)
            if res.get("status") == "error":
                return res
        logger.info(f"打印配置已更新: 内置模拟打印={'启用' if self._simulated_print_enabled else '禁用'}, "
                    f"输出目录={self._print_dir}")
        return {"status": "ok", **self.get_print_config()}

    def get_print_config(self) -> dict:
        """查询打印配置 (开关 + 打印输出目录信息)"""
        return {
            "simulated_print_enabled": self._simulated_print_enabled,
            "output_dir": self._output_dir,            # 可编辑的输出目录 (相对 DATA_DIR 或绝对路径)
            "print_dir": self.print_dir,               # 模拟电子盘路径 (展示)
            "print_dir_real": self.print_dir_real,     # 本机真实输出目录
            "file_extension": self._file_ext,
            "send_port": self._send_port,
            "recv_port": self._recv_port,
        }

    def delete_all_jobs(self, db: Session) -> dict:
        """清除全部打印任务记录"""
        try:
            deleted = db.query(PrintJob).delete()
            db.commit()
            logger.info(f"已清除全部打印任务记录: {deleted} 条")
            return {"status": "ok", "deleted": deleted}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}


# 全局单例
print_service = PrintService()
