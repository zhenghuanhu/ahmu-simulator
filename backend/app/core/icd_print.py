"""
ARINC744A 打印协议编解码 (对齐 ETest A744_print_ACK.py 的解析逻辑)

消息承载 (ICD):
  - AHMU -> 打印机 : AFDX A664 消息 Printer_GIPC_Data (VL 23247, 156 字节)
      ICD 将整个 156 字节定义为 Data_Block(BLK opaque), 内部结构为打印私有协议:
        字节 0     : optCode (0=URQ 用户请求, 1=DATA 数据块)
        字节 1-2   : blockNum (uint16 大端) — URQ=总块数, DATA=当前块号(从 1)
        字节 3-155 : payload (153 字节) — URQ=文件名(ASCII, NUL 填充), DATA=数据块
  - 打印机 -> AHMU : A429 消息
        po429_Print_BLK_Msg    : 打印块 ACK (BlockNumber)
        po429_Printer_Status_Msg : 打印机状态 / 作业状态 (JobStatus, Error)

对齐说明:
  ETest A744_print_ACK.py 中 `data[3:]` 为数据块, 故头为 3 字节 (optCode+blockNum);
  BLOCK_SIZE=150 为每块有效数据 (payload 153 字节 = 150 数据 + 3 填充)。
"""
import json
import struct
from typing import Any, Optional

# 打印操作码
OPT_URQ = 0    # User Request (用户请求)
OPT_DATA = 1   # Data Block (数据块)

# 消息尺寸
MSG_SIZE = 156          # Printer_GIPC_Data 总字节数
HEADER_SIZE = 3         # optCode(1) + blockNum(2)
PAYLOAD_SIZE = MSG_SIZE - HEADER_SIZE  # 153
BLOCK_SIZE = 150        # 每块有效数据 (对齐 ETest BLOCK_SIZE)

# 作业状态码 (对齐 ETest Status_Feedback: JobStatus=0x00 成功, Error=0x00 无错)
JOB_STATUS_SUCCESS = 0x00
JOB_STATUS_FAILED = 0x01
ERROR_NONE = 0x00


def encode_print_message(opt_code: int, block_num: int, payload: bytes = b"") -> bytes:
    """编码打印消息 (Printer_GIPC_Data 的 156 字节 Data_Block)"""
    header = struct.pack(">BH", opt_code, block_num)
    payload = payload[:PAYLOAD_SIZE]
    payload = payload + b"\x00" * (PAYLOAD_SIZE - len(payload))
    return header + payload  # 156 字节


def decode_print_message(data: bytes) -> Optional[dict[str, Any]]:
    """解码打印消息, 返回 {opt_code, block_num, payload}"""
    if len(data) < HEADER_SIZE:
        return None
    opt_code = data[0]
    block_num = struct.unpack(">H", data[1:HEADER_SIZE])[0]
    payload = data[HEADER_SIZE:]
    return {"opt_code": opt_code, "block_num": block_num, "payload": payload}


def encode_urq(filename: str, total_blocks: int) -> bytes:
    """编码 URQ (用户请求): optCode=0, blockNum=总块数, payload=文件名"""
    return encode_print_message(OPT_URQ, total_blocks, filename.encode("utf-8"))


def encode_data_block(block_num: int, block_data: bytes) -> bytes:
    """编码 DATA (数据块): optCode=1, blockNum=块号, payload=数据块"""
    return encode_print_message(OPT_DATA, block_num, block_data)


def split_blocks(content: bytes, block_size: int = BLOCK_SIZE) -> list[bytes]:
    """将打印内容按块大小切分, 返回数据块列表"""
    if not content:
        return [b""]
    return [content[i:i + block_size] for i in range(0, len(content), block_size)]


# ==================== ACK / 状态 (Mock 用 JSON over UDP, 真实为 A429 32位字) ====================

def encode_ack(block_number: int) -> bytes:
    """编码打印块 ACK (Mock: JSON, 真实 A429 po429_Print_BLK_Msg 的 BlockNumber)"""
    return json.dumps({"type": "ACK", "block_number": block_number}).encode("utf-8")


def encode_status_report(job_status: int = JOB_STATUS_SUCCESS, error: int = ERROR_NONE) -> bytes:
    """编码作业状态反馈 (Mock: JSON, 真实 A429 po429_Printer_Status_Msg)"""
    return json.dumps({"type": "STATUS", "job_status": job_status, "error": error}).encode("utf-8")


def encode_printer_status(code: int = 0) -> bytes:
    """编码打印机状态周期上报 (Mock: JSON, 真实 A429 L350 Printer_Status)"""
    return json.dumps({"type": "PRINTER_STATUS", "code": code}).encode("utf-8")


def decode_feedback(data: bytes) -> Optional[dict[str, Any]]:
    """解码打印机反馈 (ACK/STATUS/PRINTER_STATUS/RTS/CTS), 返回 dict 或 None"""
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# ==================== 块传输模式 (RTS/CTS) ====================

def encode_rts(record_count: int) -> bytes:
    """编码 RTS (Request To Send): 块传输模式打印请求, 含 RECORD 数量"""
    return json.dumps({"type": "RTS", "record_count": record_count}).encode("utf-8")


def encode_cts() -> bytes:
    """编码 CTS (Clear To Send): 打印机允许打印"""
    return json.dumps({"type": "CTS"}).encode("utf-8")
