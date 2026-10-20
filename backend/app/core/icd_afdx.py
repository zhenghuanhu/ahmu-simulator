"""
AFDX / ARINC 664 消息编解码 (基于 ICD 信号定义)

封装 ICD 信号解码能力, 供 AFDX 接收通道与测试脚本共用:
  - decode_frame: 原始字节 -> 信号字典 (复用 MessageFrame.parse_any)
  - encode_frame: 信号值 -> 原始字节 (parse_any 的逆向, 供测试脚本构造报文)
  - find_frame / find_frames_by_vl: 从 ICD 定位 A664 消息帧

与 parse_any 完全对齐: 支持 SINT/UBNR/BNR/FLOAT(IEEE754)/BOOL/DIS 等类型。
"""
import struct
from typing import Any, Optional

from app.core.icd_parser import icd_parser, MessageFrame, SignalDef


def find_frame(member_system: str, frame_name: str) -> Optional[MessageFrame]:
    """按 成员系统 + 消息帧名 定位消息帧"""
    model = icd_parser.get_member_model(member_system)
    if not model:
        return None
    return model.frames.get(frame_name)


def find_frames_by_vl(vl_id: int) -> list[MessageFrame]:
    """按虚拟链路 VL ID 查找所有 A664 消息帧"""
    frames = []
    for model in icd_parser.member_models.values():
        for frame in model.frames.values():
            if frame.protocol == "A664" and frame.vl_id == vl_id:
                frames.append(frame)
    return frames


def _encode_signal_raw(sig: SignalDef, value: Any) -> Optional[int]:
    """将信号值转换为原始位值 (parse_any 的逆向)"""
    if sig.data_type in ("pad", "opaque", "fsb", "resv", "spar"):
        return None

    # IEEE 754 浮点 (XML DataFormatType=FLOAT)
    if sig.data_format_type == "FLOAT" and sig.bit_length in (32, 64):
        pack_fmt = ">f" if sig.bit_length == 32 else ">d"
        unpack_fmt = ">I" if sig.bit_length == 32 else ">Q"
        return struct.unpack(unpack_fmt, struct.pack(pack_fmt, float(value)))[0]

    if sig.data_type == "bool":
        return 1 if value else 0

    if sig.data_type == "dis":
        return int(value)

    if sig.data_type in ("int", "uint", "float"):
        res = sig.resolution if sig.resolution else 1.0
        raw = int(round((float(value) - (sig.offset or 0.0)) / res))
        if sig.data_type == "int" and raw < 0:
            # 有符号补码
            raw += (1 << sig.bit_length)
        return raw

    return int(value)


def encode_frame(frame: MessageFrame, signal_values: dict[str, Any]) -> bytes:
    """将信号值编码为消息帧原始字节 (其他未设置位保持 0)

    signal_values: {信号名: 值}, 未提供的信号按 0 处理
    返回 message_size 字节的消息帧 (或按最高位自动扩展)。
    """
    bits = 0
    for name, sig in frame.signals.items():
        if name not in signal_values:
            continue
        raw_val = _encode_signal_raw(sig, signal_values[name])
        if raw_val is None:
            continue
        mask = (1 << sig.bit_length) - 1
        raw_val &= mask
        bits |= raw_val << sig.bit_offset

    size = frame.message_size or max(1, (bits.bit_length() + 7) // 8)
    return bits.to_bytes(size, "big")


def decode_frame(frame: MessageFrame, raw_data: bytes) -> dict[str, Any]:
    """将消息帧原始字节解码为信号字典 (含 value/unit/validity/raw 等)"""
    return frame.parse_any(raw_data)


def resolve_frame(member_system: str, frame_name: str, vl_id: Optional[int] = None) -> Optional[MessageFrame]:
    """定位消息帧: 优先按 成员+帧名, 失败时按 VL ID 兜底 (取第一条 A664 帧)"""
    frame = find_frame(member_system, frame_name)
    if frame:
        return frame
    if vl_id is not None:
        frames = find_frames_by_vl(vl_id)
        if frames:
            return frames[0]
    return None
