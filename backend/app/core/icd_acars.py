"""
ARINC619 ACARS 数据链协议编解码 (对齐 ETest acars_619_send_recv.py 的收发逻辑)

协议说明:
  ACARS 维护数据经 CPM 上的 ACARS 接口应用处理转发, 通过 ARINC664(AFDX) 总线承载。
  Mock 环境下用 UDP 单播模拟 A664 虚拟链路, 报文结构对齐 ARINC619 的 LDU 交互语义:

  1. 控制帧 (16 字节) — 对应 ETest prot_acars_init0
       字节 0     : SAL (System Address Label, 0xC3)
       字节 1-2   : Word_Count (uint16 大端)   — 携带待传数据长度/字数
       字节 3     : Des_Code (目的代码)
       字节 4     : Type (链路控制字)
       字节 5     : Length (固定 0x06)
       字节 6     : FSB (0x03, 数据帧标志)
       字节 7-15  : 保留 (0x00)

     Type 语义 (ARINC619 链路控制):
       ALO = 0x47  上行链路请求 (地面/CPM 轮询请求 AHMU 下发数据)
       ALR = 0x48  上行链路响应 (AHMU 响应: 有数据可下发)
       RTS = 0x41  请求发送 (Request To Send, 发送方发起)
       CTS = 0x42  允许发送 (Clear To Send, 接收方应答)
       ACK = 0x46  确认 (接收方确认数据接收)

  2. 数据帧 (>16 字节) — 承载 EOR 请求或维护报告正文
       字节 0-1   : Length (uint16 大端, 负载长度)
       字节 2     : FSB (0x03)
       字节 3     : SOT SAL (0x23, Start Of Text 标记)
       字节 4-5   : LDU_number (uint16 大端)   — 链路数据单元序号
       字节 6-7   : file_number (uint16 大端)  — 文件/报告序号
       字节 8..   : payload (UTF-8)
       字节 -2..  : CRC16-X25 (uint16 大端, 字节交换)

  EOR (End Of Report) 下行报告标识 — 对齐 ETest EOR_list:
       DNL_ACMF101~106/0/EOR  故障报告 (fault_report)
       DNL_ACMF201~202/0/EOR  失效报告 (failure_report)
       DNL_ACMF301/0/EOR      FDE (flight deck effect)
       DNL_ACMF401/0/EOR      构型/事件报告 (config_data / event_report)
"""
import struct
from typing import Any, Optional

# ==================== 链路常量 ====================

SAL = 0xC3              # System Address Label (控制帧)
SAL_SOT = 0x23          # Start Of Text 标记 (数据帧)
FSB = 0x03              # 数据帧标志 (Final Segment Bit)

CONTROL_FRAME_SIZE = 16  # 控制帧固定 16 字节
DATA_HEADER_SIZE = 8     # 数据帧头: Length(2)+FSB(1)+SAL(1)+LDU(2)+file(2)

# 控制帧类型 (ARINC619 链路控制字)
TYPE_ALO = 0x47   # 上行链路请求 (Acknowledge Link Order)
TYPE_ALR = 0x48   # 上行链路响应 (Acknowledge Link Response)
TYPE_RTS = 0x41   # Request To Send
TYPE_CTS = 0x42   # Clear To Send
TYPE_ACK = 0x46   # Acknowledge

TYPE_NAMES = {
    TYPE_ALO: "ALO", TYPE_ALR: "ALR",
    TYPE_RTS: "RTS", TYPE_CTS: "CTS", TYPE_ACK: "ACK",
}

# ==================== EOR 报告标识映射 ====================

# EOR 前缀 -> 报告类型 (下行维护数据分类)
EOR_FAULT = "DNL_ACMF1"      # 101~106 故障报告
EOR_FAILURE = "DNL_ACMF2"    # 201~202 失效报告
EOR_FDE = "DNL_ACMF3"        # 301 FDE
EOR_CONFIG = "DNL_ACMF4"     # 401 构型/事件报告

# 默认下行报告请求列表 (对齐 ETest EOR_list)
DEFAULT_EOR_LIST = [
    "DNL_ACMF101/0/EOR", "DNL_ACMF102/0/EOR", "DNL_ACMF103/0/EOR",
    "DNL_ACMF104/0/EOR", "DNL_ACMF105/0/EOR", "DNL_ACMF106/0/EOR",
    "DNL_ACMF201/0/EOR", "DNL_ACMF202/0/EOR",
    "DNL_ACMF301/0/EOR", "DNL_ACMF401/0/EOR",
]


def report_type_of(eor: str) -> str:
    """根据 EOR 标识返回报告类型: fault_report/failure_report/fde/config_data"""
    eor = (eor or "").upper()
    if eor.startswith(EOR_FAULT):
        return "fault_report"
    if eor.startswith(EOR_FAILURE):
        return "failure_report"
    if eor.startswith(EOR_FDE):
        return "fde"
    if eor.startswith(EOR_CONFIG):
        return "config_data"
    return "unknown"


# ==================== CRC-16/X25 ====================

def crc16_x25(data: bytes) -> int:
    """CRC-16/X25 (多项式 0x1021, 初值 0xFFFF, 用于 ARINC619 数据帧完整性校验)"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _swap16(v: int) -> int:
    return ((v & 0x00FF) << 8) | ((v & 0xFF00) >> 8)


# ==================== 控制帧编解码 ====================

def encode_control_frame(type_: int, word_count: int = 0,
                         des_code: int = 0, sal: int = SAL) -> bytes:
    """编码 ARINC619 控制帧 (16 字节)"""
    frame = struct.pack(
        ">BHBBBB", sal, word_count & 0xFFFF, des_code & 0xFF,
        type_ & 0xFF, 0x06, FSB,
    )
    # 补齐到 16 字节
    return frame + b"\x00" * (CONTROL_FRAME_SIZE - len(frame))


def decode_control_frame(data: bytes) -> Optional[dict[str, Any]]:
    """解码 ARINC619 控制帧, 返回 {sal, word_count, des_code, type, type_name} 或 None"""
    if len(data) < 7:
        return None
    sal, word_count, des_code, type_, length, fsb = struct.unpack(">BHBBBB", data[:7])
    return {
        "sal": sal,
        "word_count": word_count,
        "des_code": des_code,
        "type": type_,
        "type_name": TYPE_NAMES.get(type_, f"0x{type_:02X}"),
        "length": length,
        "fsb": fsb,
    }


# ==================== 数据帧编解码 ====================

def encode_data_frame(payload: bytes, ldu_number: int = 0,
                      file_number: int = 0) -> bytes:
    """编码 ARINC619 数据帧: header + SOT + payload + CRC"""
    payload = payload or b""
    body = struct.pack(">BHH", SAL_SOT, ldu_number & 0xFFFF, file_number & 0xFFFF)
    body += payload
    crc = crc16_x25(body)
    crc_swapped = _swap16(crc)
    length = len(body) + 2  # 负载 + CRC
    header = struct.pack(">HB", length & 0xFFFF, FSB)
    return header + body + struct.pack(">H", crc_swapped)


def decode_data_frame(data: bytes) -> Optional[dict[str, Any]]:
    """解码 ARINC619 数据帧, 校验 CRC, 返回 {payload, ldu_number, file_number, crc_ok}"""
    if len(data) < DATA_HEADER_SIZE + 2:
        return None
    length, fsb = struct.unpack(">HB", data[:3])
    sal = data[3]
    if sal != SAL_SOT:
        return None
    ldu_number, file_number = struct.unpack(">HH", data[4:8])
    payload = data[8:-2]
    crc_recv = struct.unpack(">H", data[-2:])[0]
    crc_recv = _swap16(crc_recv)
    crc_calc = crc16_x25(data[3:-2])
    return {
        "payload": payload,
        "ldu_number": ldu_number,
        "file_number": file_number,
        "crc_ok": crc_recv == crc_calc,
        "length": length,
    }


def is_control_frame(data: bytes) -> bool:
    """判断是否为控制帧 (16 字节)"""
    return len(data) == CONTROL_FRAME_SIZE


def is_data_frame(data: bytes) -> bool:
    """判断是否为数据帧 (>16 字节, 第 4 字节为 SOT SAL=0x23)"""
    return len(data) > CONTROL_FRAME_SIZE and len(data) >= 3 and data[3] == SAL_SOT


# ==================== A664 帧封装 (AHMU <-> CPM 之间的载荷承载) ====================
# AHMU 仿真器只负责: 提取数据 -> 整合维护数据包 -> 封装 A664 帧(载荷=维护数据) -> 发送。
# ARINC619 报文的封装与 664/429 转换由下游 (CPM 上的 ACARS 接口应用 + RDCU + CMU) 完成,
# 这些下游功能在模拟程序 simulate_acars_chain.py 中合并实现。

A664_MSG_DATA = 0x01   # 维护数据下行 (AHMU -> CPM)
A664_MSG_EOR = 0x02    # EOR 上行请求 (CPM -> AHMU)
A664_MSG_ACK = 0x03    # A664 总线确认 (CPM -> AHMU)

A664_HEADER_SIZE = 4   # msg_type(2) + payload_length(2)


def encode_a664_frame(msg_type: int, payload: bytes) -> bytes:
    """封装 A664 帧 (header + payload), 载荷为结构化维护数据包或 EOR 请求"""
    payload = payload or b""
    return struct.pack(">HH", msg_type & 0xFFFF, len(payload)) + payload


def decode_a664_frame(data: bytes) -> Optional[dict[str, Any]]:
    """解析 A664 帧, 返回 {msg_type, payload} 或 None"""
    if len(data) < A664_HEADER_SIZE:
        return None
    msg_type, length = struct.unpack(">HH", data[:A664_HEADER_SIZE])
    payload = data[A664_HEADER_SIZE:A664_HEADER_SIZE + length]
    return {"msg_type": msg_type, "payload": payload}
