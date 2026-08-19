"""
ICD解析引擎 + MemberModel生成器
解析ICD文件 -> 自动生成MemberModel对象 -> msgpack二进制缓存
"""
import os
import json
import struct
import asyncio
from typing import Any, Optional
from dataclasses import dataclass, field
from loguru import logger

from app.config import ICD_CACHE_FILE, CACHE_DIR


@dataclass
class SignalDef:
    """信号定义"""
    name: str
    bit_offset: int
    bit_length: int
    resolution: float = 1.0
    offset: float = 0.0
    unit: str = ""
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    data_type: str = "int"  # int/uint/float/bool


@dataclass
class MessageFrame:
    """消息帧定义"""
    message_id: str        # 成员系统-数据库-消息帧
    member_system: str
    database_name: str
    frame_name: str
    protocol: str          # A664/A429/A825
    port_id: Optional[int] = None
    label_id: Optional[int] = None
    can_id: Optional[int] = None
    vl_id: Optional[int] = None
    signals: dict[str, SignalDef] = field(default_factory=dict)

    def parse_any(self, raw_data: bytes) -> dict[str, Any]:
        """
        解析任意原始数据帧, 自动适配信号定义
        ICD变更时业务脚本无需修改
        """
        results = {}
        bits = int.from_bytes(raw_data, byteorder="big", signed=False)

        for sig_name, sig_def in self.signals.items():
            # 提取信号位段
            mask = ((1 << sig_def.bit_length) - 1)
            raw_val = (bits >> sig_def.bit_offset) & mask

            # 类型转换 + 分辨率 + 偏移
            if sig_def.data_type in ("int", "uint"):
                value = raw_val * sig_def.resolution + sig_def.offset
            elif sig_def.data_type == "float":
                # 浮点信号: 按位段提取后转float
                value = float(raw_val) * sig_def.resolution + sig_def.offset
            elif sig_def.data_type == "bool":
                value = bool(raw_val)
            else:
                value = raw_val

            # 有效性校验
            validity = "valid"
            if sig_def.min_val is not None and value < sig_def.min_val:
                validity = "out_of_range"
            elif sig_def.max_val is not None and value > sig_def.max_val:
                validity = "out_of_range"

            results[sig_name] = {
                "value": value,
                "unit": sig_def.unit,
                "validity": validity,
                "raw": raw_val,
            }
        return results


class MemberModel:
    """
    成员系统模型 - 包含该成员系统所有消息帧的解析器集合
    """

    def __init__(self, member_system: str):
        self.member_system = member_system
        self.frames: dict[str, MessageFrame] = {}  # frame_name -> MessageFrame

    def add_frame(self, frame: MessageFrame):
        self.frames[frame.frame_name] = frame

    def parse_message(self, frame_name: str, raw_data: bytes) -> dict[str, Any]:
        """解析指定消息帧"""
        frame = self.frames.get(frame_name)
        if frame is None:
            return {"error": f"未知消息帧: {frame_name}"}
        return frame.parse_any(raw_data)

    def get_signal_list(self) -> list[str]:
        """获取所有信号名称列表"""
        signals = []
        for frame in self.frames.values():
            signals.extend(frame.signals.keys())
        return signals


class ICDParser:
    """
    ICD文件解析引擎
    支持JSON格式ICD文件导入 -> 冲突检测 -> 生成MemberModel
    首次解析后缓存为msgpack二进制快照
    """

    def __init__(self):
        self.member_models: dict[str, MemberModel] = {}
        self.icd_entries: list[dict] = []
        self._is_loaded = False

    def parse_icd_file(self, file_path: str) -> dict:
        """
        解析ICD文件
        支持JSON格式, 结构:
        {
            "messages": [
                {
                    "message_id": "MEM01-DB01-FAULT_REPORT",
                    "member_system": "MEM01",
                    "database_name": "DB01",
                    "frame_name": "FAULT_REPORT",
                    "protocol": "A664",
                    "port_id": 1,
                    "vl_id": 1,
                    "signals": [
                        {"name": "fault_code", "bit_offset": 0, "bit_length": 16, ...},
                        ...
                    ]
                }
            ]
        }
        """
        logger.info(f"开始解析ICD文件: {file_path}")
        result = {
            "total_messages": 0,
            "total_signals": 0,
            "total_members": 0,
            "conflicts": [],
            "errors": [],
        }

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                icd_data = json.load(f)
        except FileNotFoundError:
            result["errors"].append(f"文件不存在: {file_path}")
            return result
        except json.JSONDecodeError as e:
            result["errors"].append(f"JSON解析失败: {e}")
            return result

        messages = icd_data.get("messages", [])
        seen_ids = set()
        seen_labels = set()
        seen_can_ids = set()
        seen_vl_ids = set()

        for msg in messages:
            msg_id = msg.get("message_id", "")
            member = msg.get("member_system", "")
            protocol = msg.get("protocol", "A664")

            # 冲突检测
            if msg_id in seen_ids:
                result["conflicts"].append(f"重复消息ID: {msg_id}")
                continue
            seen_ids.add(msg_id)

            if protocol == "A429" and msg.get("label_id"):
                if (msg.get("label_id"), msg.get("sdi", 0)) in seen_labels:
                    result["conflicts"].append(f"重复A429 Label: {msg.get('label_id')}")
                seen_labels.add((msg.get("label_id"), msg.get("sdi", 0)))

            if protocol == "A825" and msg.get("can_id"):
                if msg.get("can_id") in seen_can_ids:
                    result["conflicts"].append(f"重复CAN ID: 0x{msg.get('can_id'):X}")
                seen_can_ids.add(msg.get("can_id"))

            if protocol == "A664" and msg.get("vl_id"):
                if msg.get("vl_id") in seen_vl_ids:
                    result["conflicts"].append(f"重复VL ID: {msg.get('vl_id')}")
                seen_vl_ids.add(msg.get("vl_id"))

            # 创建消息帧
            frame = MessageFrame(
                message_id=msg_id,
                member_system=member,
                database_name=msg.get("database_name", ""),
                frame_name=msg.get("frame_name", ""),
                protocol=protocol,
                port_id=msg.get("port_id"),
                label_id=msg.get("label_id"),
                can_id=msg.get("can_id"),
                vl_id=msg.get("vl_id"),
            )

            # 解析信号
            for sig in msg.get("signals", []):
                signal = SignalDef(
                    name=sig["name"],
                    bit_offset=sig.get("bit_offset", 0),
                    bit_length=sig.get("bit_length", 1),
                    resolution=sig.get("resolution", 1.0),
                    offset=sig.get("offset", 0.0),
                    unit=sig.get("unit", ""),
                    min_val=sig.get("min_val"),
                    max_val=sig.get("max_val"),
                    data_type=sig.get("data_type", "int"),
                )
                frame.signals[signal.name] = signal
                result["total_signals"] += 1

            # 添加到MemberModel
            if member not in self.member_models:
                self.member_models[member] = MemberModel(member)
                result["total_members"] += 1
            self.member_models[member].add_frame(frame)

            # 记录ICD条目
            self.icd_entries.append({
                "message_id": msg_id,
                "member_system": member,
                "database_name": frame.database_name,
                "frame_name": frame.frame_name,
                "protocol": protocol,
                "port_id": frame.port_id,
                "label_id": frame.label_id,
                "can_id": frame.can_id,
                "vl_id": frame.vl_id,
            })

            result["total_messages"] += 1

        self._is_loaded = True
        logger.info(f"ICD解析完成: {result['total_messages']}条消息, "
                     f"{result['total_signals']}个信号, {result['total_members']}个成员系统, "
                     f"{len(result['conflicts'])}个冲突")

        # 生成二进制缓存
        self._save_cache()

        return result

    def _save_cache(self):
        """将解析结果序列化为msgpack二进制快照"""
        try:
            import msgpack
            cache_data = {
                "member_systems": {},
                "icd_entries": self.icd_entries,
            }
            for member, model in self.member_models.items():
                frames_data = {}
                for fname, frame in model.frames.items():
                    signals_data = {}
                    for sname, sig in frame.signals.items():
                        signals_data[sname] = {
                            "bit_offset": sig.bit_offset,
                            "bit_length": sig.bit_length,
                            "resolution": sig.resolution,
                            "offset": sig.offset,
                            "unit": sig.unit,
                            "min_val": sig.min_val,
                            "max_val": sig.max_val,
                            "data_type": sig.data_type,
                        }
                    frames_data[fname] = {
                        "message_id": frame.message_id,
                        "member_system": frame.member_system,
                        "database_name": frame.database_name,
                        "frame_name": frame.frame_name,
                        "protocol": frame.protocol,
                        "port_id": frame.port_id,
                        "label_id": frame.label_id,
                        "can_id": frame.can_id,
                        "vl_id": frame.vl_id,
                        "signals": signals_data,
                    }
                cache_data["member_systems"][member] = frames_data

            with open(ICD_CACHE_FILE, "wb") as f:
                msgpack.packb(cache_data, use_bin_type=True)
            logger.info(f"ICD快照已缓存: {ICD_CACHE_FILE}")
        except Exception as e:
            logger.error(f"ICD缓存保存失败: {e}")

    def load_cache(self) -> bool:
        """从二进制快照加载ICD数据 (快速启动)"""
        try:
            import msgpack
            if not os.path.exists(ICD_CACHE_FILE):
                logger.info("ICD快照不存在, 需要重新解析")
                return False

            with open(ICD_CACHE_FILE, "rb") as f:
                cache_data = msgpack.unpackb(f.read(), raw=False)

            self.member_models = {}
            self.icd_entries = cache_data.get("icd_entries", [])

            for member, frames_data in cache_data.get("member_systems", {}).items():
                model = MemberModel(member)
                for fname, fdata in frames_data.items():
                    frame = MessageFrame(
                        message_id=fdata["message_id"],
                        member_system=fdata["member_system"],
                        database_name=fdata["database_name"],
                        frame_name=fdata["frame_name"],
                        protocol=fdata["protocol"],
                        port_id=fdata.get("port_id"),
                        label_id=fdata.get("label_id"),
                        can_id=fdata.get("can_id"),
                        vl_id=fdata.get("vl_id"),
                    )
                    for sname, sdata in fdata.get("signals", {}).items():
                        frame.signals[sname] = SignalDef(
                            name=sname,
                            bit_offset=sdata["bit_offset"],
                            bit_length=sdata["bit_length"],
                            resolution=sdata.get("resolution", 1.0),
                            offset=sdata.get("offset", 0.0),
                            unit=sdata.get("unit", ""),
                            min_val=sdata.get("min_val"),
                            max_val=sdata.get("max_val"),
                            data_type=sdata.get("data_type", "int"),
                        )
                    model.add_frame(frame)
                self.member_models[member] = model

            self._is_loaded = True
            logger.info(f"ICD快照加载完成: {len(self.member_models)}个成员系统")
            return True
        except Exception as e:
            logger.error(f"ICD快照加载失败: {e}")
            return False

    def get_member_model(self, member_system: str) -> Optional[MemberModel]:
        """获取指定成员系统的模型"""
        return self.member_models.get(member_system)

    def get_all_members(self) -> list[str]:
        """获取所有成员系统列表"""
        return list(self.member_models.keys())

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    def generate_demo_icd(self, output_path: str, member_count: int = 20):
        """生成演示ICD文件 (用于本地测试)"""
        messages = []
        for i in range(1, member_count + 1):
            member = f"MEM{i:03d}"
            # 故障报告消息帧
            messages.append({
                "message_id": f"{member}-DB01-FAULT_REPORT",
                "member_system": member,
                "database_name": "DB01",
                "frame_name": "FAULT_REPORT",
                "protocol": "A664",
                "vl_id": (i % 10) + 1,
                "port_id": i,
                "signals": [
                    {"name": "fault_code", "bit_offset": 0, "bit_length": 16,
                     "resolution": 1.0, "unit": "", "data_type": "uint",
                     "min_val": 0, "max_val": 9999},
                    {"name": "severity", "bit_offset": 16, "bit_length": 4,
                     "resolution": 1.0, "unit": "", "data_type": "uint",
                     "min_val": 0, "max_val": 3},
                    {"name": "flight_segment", "bit_offset": 24, "bit_length": 8,
                     "resolution": 1.0, "unit": "", "data_type": "int",
                     "min_val": -128, "max_val": 127},
                ]
            })
            # 状态参数消息帧
            messages.append({
                "message_id": f"{member}-DB01-STATUS_PARAM",
                "member_system": member,
                "database_name": "DB01",
                "frame_name": "STATUS_PARAM",
                "protocol": "A429",
                "label_id": i % 256,
                "signals": [
                    {"name": "temperature", "bit_offset": 11, "bit_length": 11,
                     "resolution": 0.1, "offset": -50.0, "unit": "C",
                     "data_type": "float", "min_val": -50, "max_val": 150},
                    {"name": "voltage", "bit_offset": 22, "bit_length": 8,
                     "resolution": 0.1, "unit": "V",
                     "data_type": "float", "min_val": 0, "max_val": 30},
                ]
            })

        icd_data = {"messages": messages}
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(icd_data, f, ensure_ascii=False, indent=2)
        logger.info(f"演示ICD已生成: {output_path} ({len(messages)}条消息)")
        return output_path


# 全局单例
icd_parser = ICDParser()
