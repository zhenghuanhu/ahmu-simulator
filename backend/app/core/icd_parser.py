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
    """信号定义

    兼容两种 ICD 来源:
    - JSON (自研简化格式): bit_offset 为消息内绝对位偏移
    - XML (Member_System_all_sender_icd_v6_1_1): 完整保留航电 ICD 字段
    """
    name: str
    bit_offset: int              # 消息内绝对位偏移 = ByteOffsetWithinMsg*8 + BitOffsetWithinDS
    bit_length: int              # 位长度 (XML: ParameterSize)
    resolution: float = 1.0      # 分辨率 (XML: LsbRes*Multiplier)
    offset: float = 0.0          # 偏移量
    unit: str = ""               # 单位 (XML: Units)
    min_val: Optional[float] = None   # 最小值 (XML: FuncRngMin/FullScaleRngMin)
    max_val: Optional[float] = None   # 最大值 (XML: FuncRngMax/FullScaleRngMax)
    data_type: str = "int"       # 内部类型: int/uint/float/bool/dis/char/pad/opaque
    # --- XML 原始字段 (可追溯) ---
    data_format_type: str = ""   # XML 原始 DataFormatType (SINT/UBNR/BNR/BOOL/DIS/...)
    byte_offset: int = 0         # 消息内字节偏移 (XML: DS.ByteOffsetWithinMsg)
    ds_bit_offset: int = 0       # DS 内位偏移 (XML: DP.BitOffsetWithinDS)
    lsb_res: Optional[float] = None     # LSB 分辨率 (XML: LsbRes)
    multiplier: Optional[float] = None  # 倍率 (XML: Multiplier)
    ohms_attribute: str = ""     # OHMS 属性 (Fault/Parameter/ConfigurationReport)
    one_state: str = ""          # 1 状态描述 (XML: OneState)
    zero_state: str = ""         # 0 状态描述 (XML: ZeroState)
    label: Optional[str] = None  # RDCU Label (XML: Label)


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
    # --- XML 扩展字段 ---
    message_size: int = 0                 # 消息字节大小 (XML: MessageSize)
    transmission_interval: int = 0        # 发送周期 ms (XML: TransmissionIntervalMinimum)
    msg_data_protocol_type: str = ""      # 消息数据协议类型 (XML: MsgDataProtocolType)
    port_name: str = ""                   # 端口名 (XML: Port.Name)
    ds_list: list = field(default_factory=list)  # DS 分组列表 (XML 可追溯)
    signals: dict[str, SignalDef] = field(default_factory=dict)

    def parse_any(self, raw_data: bytes) -> dict[str, Any]:
        """
        解析任意原始数据帧, 自动适配信号定义
        ICD变更时业务脚本无需修改
        支持有符号补码(SINT/BNR)、无符号(UBNR/UINT)、布尔、离散量、字符类型
        """
        results = {}
        bits = int.from_bytes(raw_data, byteorder="big", signed=False)

        for sig_name, sig_def in self.signals.items():
            # 填充/帧同步/保留位跳过
            if sig_def.data_type in ("pad", "opaque", "fsb", "resv", "spar"):
                continue

            # 提取信号位段
            mask = ((1 << sig_def.bit_length) - 1) if sig_def.bit_length < 64 else None
            if mask is None:
                raw_val = (bits >> sig_def.bit_offset) if sig_def.bit_offset < 64 else 0
            else:
                raw_val = (bits >> sig_def.bit_offset) & mask

            # 类型转换 + 分辨率 + 偏移
            if sig_def.data_type == "int":
                # 有符号补码: 符号扩展
                sign_bit = 1 << (sig_def.bit_length - 1)
                if raw_val & sign_bit:
                    raw_val -= (1 << sig_def.bit_length)
                value = raw_val * sig_def.resolution + sig_def.offset
            elif (sig_def.data_type == "float" and sig_def.data_format_type == "FLOAT"
                  and sig_def.bit_length in (32, 64)):
                # IEEE 754 单/双精度浮点 (XML DataFormatType=FLOAT)
                raw_bytes = raw_val.to_bytes(sig_def.bit_length // 8, "big")
                value = struct.unpack(">f" if sig_def.bit_length == 32 else ">d", raw_bytes)[0]
            elif sig_def.data_type in ("uint", "float"):
                value = raw_val * sig_def.resolution + sig_def.offset
            elif sig_def.data_type == "bool":
                value = bool(raw_val)
                # 布尔类型附状态描述
                state = sig_def.one_state if raw_val else sig_def.zero_state
                results[sig_name] = {
                    "value": value, "unit": sig_def.unit, "validity": "valid",
                    "raw": raw_val, "state": state,
                }
                continue
            elif sig_def.data_type == "dis":
                # 离散量: 附状态描述
                state = sig_def.one_state if raw_val else sig_def.zero_state
                results[sig_name] = {
                    "value": raw_val, "unit": sig_def.unit, "validity": "valid",
                    "raw": raw_val, "state": state,
                }
                continue
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
        self.import_log: list[dict] = []  # 导入日志 [{step, message, level, timestamp}]
        self._is_loaded = False

    def _log(self, step: str, message: str, level: str = "info"):
        """记录导入日志"""
        from datetime import datetime
        self.import_log.append({
            "step": step,
            "message": message,
            "level": level,  # info/warning/error
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })

    def _validate_message(self, msg: dict, idx: int) -> list[str]:
        """
        校验单条消息定义, 返回错误列表
        覆盖: 必填字段、协议识别、信号字段合法性
        """
        errors = []
        # 必填字段
        msg_id = msg.get("message_id", "")
        member = msg.get("member_system", "")
        frame_name = msg.get("frame_name", "")
        protocol = (msg.get("protocol") or "").upper()

        if not msg_id:
            errors.append(f"第{idx+1}条消息缺少 message_id")
        elif msg_id.count("-") != 2:
            errors.append(f"消息ID格式错误(应为 成员系统-数据库-消息帧): {msg_id}")
        if not member:
            errors.append(f"消息 {msg_id or idx+1} 缺少 member_system")
        if not frame_name:
            errors.append(f"消息 {msg_id or idx+1} 缺少 frame_name")

        # 协议识别
        VALID_PROTOCOLS = {"A664", "A429", "A825"}
        if not protocol:
            errors.append(f"消息 {msg_id or idx+1} 缺少 protocol")
        elif protocol not in VALID_PROTOCOLS:
            errors.append(f"消息 {msg_id or idx+1} 协议无法识别: {protocol} (支持 {sorted(VALID_PROTOCOLS)})")

        # 协议相关标识
        if protocol == "A664" and not msg.get("vl_id"):
            errors.append(f"消息 {msg_id} 为A664协议但缺少 vl_id")
        if protocol == "A429" and msg.get("label_id") is None:
            errors.append(f"消息 {msg_id} 为A429协议但缺少 label_id")
        if protocol == "A825" and msg.get("can_id") is None:
            errors.append(f"消息 {msg_id} 为A825协议但缺少 can_id")

        # 信号校验
        signals = msg.get("signals", [])
        if not signals:
            errors.append(f"消息 {msg_id} 未定义任何信号")
        seen_sig = set()
        for s in signals:
            if not s.get("name"):
                errors.append(f"消息 {msg_id} 存在无名信号")
                continue
            if s["name"] in seen_sig:
                errors.append(f"消息 {msg_id} 存在重复信号名: {s['name']}")
            seen_sig.add(s.get("name"))
            bl = s.get("bit_length", 1)
            if not isinstance(bl, int) or bl <= 0 or bl > 64:
                errors.append(f"消息 {msg_id} 信号 {s['name']} 位长度非法: {bl}")
            bo = s.get("bit_offset", 0)
            if not isinstance(bo, int) or bo < 0:
                errors.append(f"消息 {msg_id} 信号 {s['name']} 位偏移非法: {bo}")
        return errors

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

        # 重建模式: 清空旧数据, ICD变更后重新导入自动重建数据库与所有MemberModel对象
        self.member_models = {}
        self.icd_entries = []
        self.import_log = []
        self._is_loaded = False

        # 格式识别: XML (Member_System_all_sender_icd_v6_1_1.xml) -> XML 解析引擎
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".xml":
            return self._parse_xml_file(file_path)

        result = {
            "total_messages": 0,
            "total_signals": 0,
            "total_members": 0,
            "conflicts": [],
            "errors": [],
            "import_log": self.import_log,
        }

        # 1. 格式识别
        if not os.path.exists(file_path):
            result["errors"].append(f"文件不存在: {file_path}")
            self._log("格式识别", f"文件不存在: {file_path}", "error")
            return result
        if ext != ".json":
            self._log("格式识别", f"文件扩展名 {ext} 非标准 JSON 格式", "warning")
        self._log("格式识别", f"识别为 {ext or '未知'} 格式 ICD 文件: {os.path.basename(file_path)}", "info")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                icd_data = json.load(f)
        except json.JSONDecodeError as e:
            result["errors"].append(f"JSON解析失败: {e}")
            self._log("格式识别", f"JSON解析失败: {e}", "error")
            return result

        # 2. 结构校验
        if not isinstance(icd_data, dict) or "messages" not in icd_data:
            result["errors"].append("ICD文件结构错误: 缺少顶层 messages 字段")
            self._log("文件校验", "文件结构错误: 缺少顶层 messages 字段", "error")
            return result
        messages = icd_data.get("messages", [])
        self._log("文件校验", f"文件结构校验通过, 共 {len(messages)} 条消息定义", "info")

        seen_ids = set()
        seen_labels = set()
        seen_can_ids = set()
        seen_vl_ids = set()

        for idx, msg in enumerate(messages):
            msg_id = msg.get("message_id", "")
            member = msg.get("member_system", "")
            protocol = (msg.get("protocol") or "A664").upper()

            # 逐条字段校验
            msg_errors = self._validate_message(msg, idx)
            if msg_errors:
                for e in msg_errors:
                    result["errors"].append(e)
                    self._log("文件校验", e, "warning")
                continue

            # 冲突检测 (重复 Label/CAN ID/VL ID)
            if msg_id in seen_ids:
                result["conflicts"].append(f"重复消息ID: {msg_id}")
                self._log("冲突检测", f"重复消息ID: {msg_id}", "warning")
                continue
            seen_ids.add(msg_id)

            if protocol == "A429" and msg.get("label_id") is not None:
                key = (msg.get("label_id"), msg.get("sdi", 0))
                if key in seen_labels:
                    result["conflicts"].append(f"重复A429 Label: {msg.get('label_id')}")
                    self._log("冲突检测", f"重复A429 Label: {msg.get('label_id')}", "warning")
                seen_labels.add(key)

            if protocol == "A825" and msg.get("can_id") is not None:
                if msg.get("can_id") in seen_can_ids:
                    result["conflicts"].append(f"重复CAN ID: 0x{msg.get('can_id'):X}")
                    self._log("冲突检测", f"重复CAN ID: 0x{msg.get('can_id'):X}", "warning")
                seen_can_ids.add(msg.get("can_id"))

            if protocol == "A664" and msg.get("vl_id") is not None:
                if msg.get("vl_id") in seen_vl_ids:
                    result["conflicts"].append(f"重复VL ID: {msg.get('vl_id')}")
                    self._log("冲突检测", f"重复VL ID: {msg.get('vl_id')}", "warning")
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

            # 解析信号 -> 信号字典
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

            # 添加到MemberModel (消息解析器集合)
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
        self._log("解析入库", f"成功解析 {result['total_messages']} 条消息帧 / "
                             f"{result['total_signals']} 个信号 / {result['total_members']} 个成员系统", "info")
        if result["conflicts"]:
            self._log("冲突检测", f"共发现 {len(result['conflicts'])} 处冲突定义", "warning")
        logger.info(f"ICD解析完成: {result['total_messages']}条消息, "
                     f"{result['total_signals']}个信号, {result['total_members']}个成员系统, "
                     f"{len(result['conflicts'])}个冲突, {len(result['errors'])}个错误")

        # 生成二进制缓存
        self._save_cache()

        return result

    def _parse_xml_file(self, file_path: str) -> dict:
        """
        解析 XML 格式 ICD 文件 (Member_System_all_sender_icd_v6_1_1.xml)
        流式提取 MemberSystem/端口/消息/DS/DP 全结构, 生成完整解析报告
        """
        from app.core.icd_xml_parser import parse_member_system_xml

        self._log("格式识别", f"识别为 XML 格式 ICD 文件: {os.path.basename(file_path)}", "info")

        parsed = parse_member_system_xml(file_path)
        if parsed.get("status") != "ok":
            self._log("格式识别", parsed.get("message", "XML 解析失败"), "error")
            return {
                "total_messages": 0, "total_signals": 0, "total_members": 0,
                "conflicts": [], "errors": [parsed.get("message", "XML 解析失败")],
                "format": "xml", "import_log": self.import_log,
                "report": parsed.get("report", {}),
            }

        self.member_models = parsed["member_models"]
        self.icd_entries = parsed["icd_entries"]
        self._is_loaded = True

        report = parsed["report"]
        self._log("文件校验", f"XML 结构校验通过: {report['total_members']} 成员 / "
                              f"{report['total_messages']} 消息 / {report['total_datasets']} DS / "
                              f"{report['total_signals']} 信号", "info")
        if report["errors"]:
            self._log("文件校验", f"发现 {len(report['errors'])} 处错误", "error")
        if report["missing_required"]:
            self._log("完整性校验", f"发现 {len(report['missing_required'])} 处必填字段缺失", "warning")
        if report["conflicts"]:
            self._log("冲突检测", f"发现 {len(report['conflicts'])} 处冲突定义", "warning")
        if report["duplicate_signals"]:
            self._log("冲突检测", f"{report['duplicate_signals']} 个重名信号已自动加后缀保留", "info")
        self._log("解析入库", f"成功解析 {report['total_messages']} 条消息 / "
                              f"{report['total_signals']} 个信号", "info")

        logger.info(f"XML ICD 解析完成: {report['total_members']} 成员, "
                     f"{report['total_messages']} 消息, {report['total_signals']} 信号")

        # 生成二进制缓存
        self._save_cache()

        return {
            "total_messages": report["total_messages"],
            "total_signals": report["total_signals"],
            "total_members": report["total_members"],
            "total_datasets": report["total_datasets"],
            "conflicts": report["conflicts"],
            "errors": report["errors"],
            "format": "xml",
            "import_log": self.import_log,
            "report": report,
        }

    def _save_cache(self):
        """将解析结果序列化为msgpack二进制快照"""
        try:
            import msgpack
            from dataclasses import asdict
            cache_data = {
                "member_models": {
                    m: {fn: asdict(fr) for fn, fr in model.frames.items()}
                    for m, model in self.member_models.items()
                },
                "icd_entries": self.icd_entries,
            }

            with open(ICD_CACHE_FILE, "wb") as f:
                f.write(msgpack.packb(cache_data, use_bin_type=True))
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

            for member, frames_data in cache_data.get("member_models", {}).items():
                model = MemberModel(member)
                for fname, fdata in frames_data.items():
                    signals = {
                        sn: SignalDef(**sd) for sn, sd in fdata.pop("signals", {}).items()
                    }
                    frame = MessageFrame(**fdata, signals=signals)
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

    def get_all_frames(self, member: Optional[str] = None) -> list[dict]:
        """
        获取消息帧列表 (摘要)
        供前端展示功能模型: 消息帧标识 + 协议 + 信号数
        """
        frames = []
        members = [member] if member else self.member_models.keys()
        for m in members:
            model = self.member_models.get(m)
            if not model:
                continue
            for fname, frame in model.frames.items():
                frames.append({
                    "message_id": frame.message_id,
                    "member_system": frame.member_system,
                    "database_name": frame.database_name,
                    "frame_name": frame.frame_name,
                    "protocol": frame.protocol,
                    "port_id": frame.port_id,
                    "label_id": frame.label_id,
                    "can_id": frame.can_id,
                    "vl_id": frame.vl_id,
                    "message_size": frame.message_size,
                    "transmission_interval": frame.transmission_interval,
                    "port_name": frame.port_name,
                    "signal_count": len(frame.signals),
                })
        return frames

    def get_frame_detail(self, member_system: str, frame_name: str) -> Optional[dict]:
        """
        获取指定消息帧详情: 消息帧标识 + 完整信号字典
        信号字典: 信号名称、位段、分辨率、偏移量、单位等完整元数据
        """
        model = self.member_models.get(member_system)
        if not model:
            return None
        frame = model.frames.get(frame_name)
        if not frame:
            return None
        signals = []
        for sname, sig in frame.signals.items():
            signals.append({
                "name": sig.name,
                "bit_offset": sig.bit_offset,
                "bit_length": sig.bit_length,
                "byte_offset": sig.byte_offset,
                "resolution": sig.resolution,
                "offset": sig.offset,
                "unit": sig.unit,
                "min_val": sig.min_val,
                "max_val": sig.max_val,
                "data_type": sig.data_type,
                "data_format_type": sig.data_format_type,
                "lsb_res": sig.lsb_res,
                "multiplier": sig.multiplier,
                "ohms_attribute": sig.ohms_attribute,
                "one_state": sig.one_state,
                "zero_state": sig.zero_state,
                "label": sig.label,
            })
        return {
            "message_id": frame.message_id,
            "member_system": frame.member_system,
            "database_name": frame.database_name,
            "frame_name": frame.frame_name,
            "protocol": frame.protocol,
            "port_id": frame.port_id,
            "label_id": frame.label_id,
            "can_id": frame.can_id,
            "vl_id": frame.vl_id,
            "message_size": frame.message_size,
            "transmission_interval": frame.transmission_interval,
            "msg_data_protocol_type": frame.msg_data_protocol_type,
            "port_name": frame.port_name,
            "signals": signals,
        }

    def parse_demo(self, member_system: str, frame_name: str, raw_hex: str) -> dict:
        """
        parse_any 解析演示: 将 hex 原始数据解析为信号字典
        ICD变更后 parse_any 自动适配新定义, 业务脚本无需修改
        """
        model = self.member_models.get(member_system)
        if not model:
            return {"error": f"成员系统不存在: {member_system}"}
        try:
            raw = bytes.fromhex(raw_hex.replace(" ", "").replace("0x", ""))
        except ValueError:
            return {"error": f"非法 hex 数据: {raw_hex}"}
        frame = model.frames.get(frame_name)
        if not frame:
            return {"error": f"未知消息帧: {frame_name}"}
        return {
            "message_id": frame.message_id,
            "frame_name": frame_name,
            "parsed": frame.parse_any(raw),
        }

    def get_signal_entries(self) -> list[dict]:
        """
        获取信号级条目列表 (入库用)
        每个信号一条记录, 供写入 ICDEntry 表
        """
        entries = []
        for member, model in self.member_models.items():
            for fname, frame in model.frames.items():
                for sname, sig in frame.signals.items():
                    entries.append({
                        "message_id": frame.message_id,
                        "member_system": frame.member_system,
                        "database_name": frame.database_name,
                        "frame_name": frame.frame_name,
                        "protocol": frame.protocol,
                        "port_id": frame.port_id,
                        "label_id": frame.label_id,
                        "can_id": frame.can_id,
                        "vl_id": frame.vl_id,
                        "signal_name": sig.name,
                        "bit_offset": sig.bit_offset,
                        "bit_length": sig.bit_length,
                        "byte_offset": sig.byte_offset,
                        "resolution": sig.resolution,
                        "offset": sig.offset,
                        "unit": sig.unit,
                        "min_val": sig.min_val,
                        "max_val": sig.max_val,
                        "data_format_type": sig.data_format_type,
                        "lsb_res": sig.lsb_res,
                        "multiplier": sig.multiplier,
                        "ohms_attribute": sig.ohms_attribute,
                        "one_state": sig.one_state,
                        "zero_state": sig.zero_state,
                        "label": sig.label,
                        "message_size": frame.message_size,
                        "transmission_interval": frame.transmission_interval,
                        "port_name": frame.port_name,
                    })
        return entries

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
