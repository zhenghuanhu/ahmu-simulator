"""
ICD XML 解析引擎 (Member_System_all_sender_icd_v6_1_1.xml)

解析航电标准 ICD XML 文件, 提取完整结构:
    MemberSystems -> MemberSystem -> Port -> Message -> DS -> DP

支持消息类型:
    - A664Message (AFDX, 嵌套于 HFQueuingPort/HFSamplingPort/A653QueuingPort/A653SamplingPort)
    - CANMessage  (CAN/A825, 嵌套于 CANPort)
    - A429Channel -> A429Word (ARINC429, 嵌套于 A429Port)
    - AnalogPort -> AnalogDiscreteParameter/AnalogSignalParameter (模拟量)

采用 ElementTree.iterparse 流式解析, 逐 MemberSystem 处理后立即释放,
避免 63MB 大文件全量加载内存。

产出:
    member_models: {member_name: MemberModel}   (消息帧 + 信号字典)
    icd_entries:   [signal dict]                (入库用)
    report:        完整解析报告 (统计/冲突/异常/缺失字段/分布)
"""
import os
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any, Optional

from loguru import logger

from app.core.icd_parser import MemberModel, MessageFrame, SignalDef


# ==================== 数据类型映射 ====================
# XML DataFormatType -> 内部 data_type (用于 parse_any 与展示)
DATAFORMAT_TYPE_MAP = {
    "SINT": "int",              # 有符号整数 (二进制补码)
    "UBNR": "uint",             # 无符号二进制数
    "UINT": "uint",             # 无符号整数
    "BNR": "float",             # 二进制补码分数 (带 LsbRes)
    "BCD": "uint",              # BCD 编码
    "FLOAT": "float",           # 浮点
    "BOOL": "bool",             # 布尔量
    "DIS": "dis",               # 离散量
    "COD": "uint",              # 编码
    "ISO-5": "char",            # ISO-5 字符
    "CHAR": "char",             # 字符
    "CHAR STRING": "char",      # 字符串
    "ISO-5 STRING": "char",     # ISO-5 字符串
    "HEX": "opaque",            # 十六进制
    "PAD": "pad",               # 填充位
    "RESV": "resv",             # 保留位
    "SPAR": "spar",             # 备用位
    "OPAQUE": "opaque",         # 不透明数据
    "BLK": "opaque",            # 块数据
    "CRC": "opaque",            # CRC 校验
    "A664_FSB": "fsb",          # A664 帧同步
    "A429OCTLBL": "opaque",     # A429 标签
    "A429SDI": "opaque",        # A429 SDI
    "A429PARITY": "opaque",     # A429 奇偶校验
    "A429_SSM_BNR": "opaque",   # A429 SSM (BNR)
    "A429_SSM_DIS": "opaque",   # A429 SSM (DIS)
    "A429_SSM_BCD": "opaque",   # A429 SSM (BCD)
    "A429_SSM_CUSTOM": "opaque",  # A429 SSM (自定义)
}

# 协议映射 (端口类型 -> protocol)
PORT_PROTOCOL_MAP = {
    "HFQueuingPort": "A664",
    "HFSamplingPort": "A664",
    "A653QueuingPort": "A664",
    "A653SamplingPort": "A664",
    "CANPort": "A825",
    "A429Port": "A429",
    "AnalogPort": "ANALOG",
}

# DP 必填字段 (缺失视为异常)
DP_REQUIRED_ATTRS = ("Name", "DataFormatType", "ParameterSize")

# DP 可选字段 (缺失为正常, 取决于类型)
DP_OPTIONAL_ATTRS = (
    "BitOffsetWithinDS", "LsbRes", "Multiplier", "Units",
    "OneState", "ZeroState", "FullScaleRngMin", "FullScaleRngMax",
    "FuncRngMin", "FuncRngMax", "OHMSAttribute", "Label",
)

# 结构性/占位信号 (帧同步/填充/保留/校验), 重名属正常现象, 不算冲突
STRUCTURAL_DATAFORMATS = {
    "PAD", "A664_FSB", "RESV", "SPAR", "A429PARITY", "A429SDI", "CRC",
}


def _to_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """安全转 int, 失败返回 default"""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _to_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转 float, 失败返回 default"""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


class XmlIcdParser:
    """Member_System XML 格式 ICD 解析器"""

    def __init__(self):
        self.member_models: dict[str, MemberModel] = {}
        self.icd_entries: list[dict] = []

        # 冲突/异常/缺失
        self.conflicts: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.missing_required: list[str] = []
        self.missing_optional: Counter = Counter()

        # 统计
        self.total_members = 0
        self.total_messages = 0
        self.total_datasets = 0
        self.total_signals = 0
        self.port_distribution: Counter = Counter()
        self.protocol_distribution: Counter = Counter()
        self.dataformat_distribution: Counter = Counter()
        self.ohms_distribution: Counter = Counter()

        # 冲突检测集合
        self._seen_message_ids: set = set()
        self._seen_vl_ids: set = set()
        self._seen_can_ids: set = set()
        self.vl_reuse_count = 0    # VL ID 复用次数 (AFDX 正常现象, 仅统计)
        self.can_reuse_count = 0   # CAN ID 复用次数
        self.duplicate_signals = 0  # 业务信号重名数 (自动加后缀保留)

        # 成员系统 ATA 映射 (Name -> ATA 章节号, 取前 2 位)
        self._member_ata: dict[str, str] = {}

    # ==================== 信号提取 ====================

    def _extract_signal(self, dp: ET.Element, ds: Optional[ET.Element],
                        member: str, frame_name: str, protocol: str) -> Optional[SignalDef]:
        """从 DP 元素提取信号定义"""
        name = dp.get("Name", "").strip()
        data_format_type = dp.get("DataFormatType", "").strip()

        # 必填字段校验
        if not name:
            self.missing_required.append(f"{member}/{frame_name}: DP 缺少 Name")
        if not data_format_type:
            self.missing_required.append(f"{member}/{frame_name}: DP 缺少 DataFormatType")
        if dp.get("ParameterSize") is None:
            self.missing_required.append(f"{member}/{frame_name}/{name or '?'}: 缺少 ParameterSize")
            return None

        bit_length = _to_int(dp.get("ParameterSize"), 1) or 1
        ds_bit_offset = _to_int(dp.get("BitOffsetWithinDS"), 0) or 0

        # 消息内字节偏移 (DS.ByteOffsetWithinMsg) + DS 内位偏移
        byte_offset = 0
        if ds is not None:
            byte_offset = _to_int(ds.get("ByteOffsetWithinMsg"), 0) or 0
        abs_bit_offset = byte_offset * 8 + ds_bit_offset

        # 可选字段
        lsb_res = _to_float(dp.get("LsbRes"))
        multiplier = _to_float(dp.get("Multiplier"))
        resolution = (lsb_res if lsb_res is not None else 1.0) * (multiplier if multiplier is not None else 1.0)

        ohms_attr = (dp.get("OHMSAttribute") or "").strip()
        one_state = (dp.get("OneState") or "").strip()
        zero_state = (dp.get("ZeroState") or "").strip()

        # 范围: 优先功能范围, 否则满量程范围
        min_val = _to_float(dp.get("FuncRngMin"))
        max_val = _to_float(dp.get("FuncRngMax"))
        if min_val is None:
            min_val = _to_float(dp.get("FullScaleRngMin"))
        if max_val is None:
            max_val = _to_float(dp.get("FullScaleRngMax"))

        # 内部类型映射
        internal_type = DATAFORMAT_TYPE_MAP.get(data_format_type, "opaque")
        if data_format_type not in DATAFORMAT_TYPE_MAP:
            self.warnings.append(
                f"{member}/{frame_name}/{name}: 未知 DataFormatType '{data_format_type}'")

        # 统计
        self.dataformat_distribution[data_format_type] += 1
        if ohms_attr:
            self.ohms_distribution[ohms_attr] += 1

        return SignalDef(
            name=name,
            bit_offset=abs_bit_offset,
            bit_length=bit_length,
            resolution=resolution,
            offset=0.0,
            unit=(dp.get("Units") or "").strip(),
            min_val=min_val,
            max_val=max_val,
            data_type=internal_type,
            data_format_type=data_format_type,
            byte_offset=byte_offset,
            ds_bit_offset=ds_bit_offset,
            lsb_res=lsb_res,
            multiplier=multiplier,
            ohms_attribute=ohms_attr,
            one_state=one_state,
            zero_state=zero_state,
            label=dp.get("Label"),
        )

    # ==================== A664 消息处理 ====================

    def _process_a664_message(self, msg: ET.Element, member: str, port_name: str,
                              port_protocol: str, member_model: MemberModel) -> None:
        """处理 A664Message (DS -> DP)"""
        msg_name = msg.get("Name", "").strip()
        vl_id = _to_int(msg.get("VLID"))
        msg_size = _to_int(msg.get("MessageSize"), 0) or 0
        interval = _to_int(msg.get("TransmissionIntervalMinimum"), 0) or 0
        proto_type = (msg.get("MsgDataProtocolType") or "").strip()

        if not msg_name:
            self.errors.append(f"{member}: A664Message 缺少 Name")
            return

        message_id = f"{member}-{msg_name}"
        if message_id in self._seen_message_ids:
            self.conflicts.append(f"重复消息: {message_id}")
            return
        self._seen_message_ids.add(message_id)

        # VL ID 复用检测 (AFDX 一个 VL 可承载多个消息, 复用属正常, 仅统计)
        if vl_id is not None and vl_id in self._seen_vl_ids:
            self.vl_reuse_count += 1
        elif vl_id is not None:
            self._seen_vl_ids.add(vl_id)

        frame = MessageFrame(
            message_id=message_id,
            member_system=member,
            database_name="",
            frame_name=msg_name,
            protocol=port_protocol,
            vl_id=vl_id,
            message_size=msg_size,
            transmission_interval=interval,
            msg_data_protocol_type=proto_type,
            port_name=port_name,
        )

        # 遍历 DS -> DP
        for ds in msg.findall("DS"):
            self.total_datasets += 1
            ds_list_entry = {
                "name": ds.get("Name", ""),
                "byte_offset": _to_int(ds.get("ByteOffsetWithinMsg"), 0) or 0,
                "size": _to_int(ds.get("DataSetSize"), 0) or 0,
            }
            frame.ds_list.append(ds_list_entry)

            for dp in ds.findall("DP"):
                sig = self._extract_signal(dp, ds, member, msg_name, port_protocol)
                if sig is None:
                    continue
                self._add_signal(frame, sig)

        member_model.add_frame(frame)
        self.total_messages += 1
        self.protocol_distribution[port_protocol] += 1

    # ==================== CAN 消息处理 ====================

    def _process_can_message(self, msg: ET.Element, member: str, port: ET.Element,
                             member_model: MemberModel) -> None:
        """处理 CANMessage (DP)"""
        msg_name = msg.get("Name", "").strip()
        can_id = _to_int(port.get("MessageID"))
        msg_size = _to_int(msg.get("MessageSize"), 0) or 0
        interval = _to_int(msg.get("TransmissionIntervalMinimum"), 0) or 0

        if not msg_name:
            self.errors.append(f"{member}: CANMessage 缺少 Name")
            return

        message_id = f"{member}-{msg_name}"
        if message_id in self._seen_message_ids:
            self.conflicts.append(f"重复消息: {message_id}")
            return
        self._seen_message_ids.add(message_id)

        if can_id is not None and can_id in self._seen_can_ids:
            self.can_reuse_count += 1
        elif can_id is not None:
            self._seen_can_ids.add(can_id)

        frame = MessageFrame(
            message_id=message_id,
            member_system=member,
            database_name="",
            frame_name=msg_name,
            protocol="A825",
            can_id=can_id,
            message_size=msg_size,
            transmission_interval=interval,
            port_name=port.get("Name", ""),
        )

        for dp in msg.findall("DP"):
            sig = self._extract_signal(dp, None, member, msg_name, "A825")
            if sig is None:
                continue
            self._add_signal(frame, sig)

        member_model.add_frame(frame)
        self.total_messages += 1
        self.protocol_distribution["A825"] += 1

    # ==================== A429 处理 ====================

    def _process_a429_channel(self, channel: ET.Element, member: str, port: ET.Element,
                              member_model: MemberModel) -> None:
        """处理 A429Channel (A429Word -> DP)"""
        ch_name = channel.get("Name", "").strip()
        if not ch_name:
            ch_name = f"A429Channel_{self.total_messages}"

        message_id = f"{member}-{ch_name}"
        if message_id in self._seen_message_ids:
            self.conflicts.append(f"重复消息: {message_id}")
            return
        self._seen_message_ids.add(message_id)

        # 尝试从首个 word 名提取 label (形如 L226_xxx)
        frame = MessageFrame(
            message_id=message_id,
            member_system=member,
            database_name="",
            frame_name=ch_name,
            protocol="A429",
            label_id=None,
            port_name=port.get("Name", ""),
            transmission_interval=_to_int(channel.get("TransmissionIntervalMinimum"), 0) or 0,
        )

        for word in channel.findall("A429Word"):
            word_name = word.get("Name", "").strip()
            word_bit_offset = _to_int(word.get("BitOffsetWithinDS"), 0) or 0
            for dp in word.findall("DP"):
                sig = self._extract_signal(dp, None, member, ch_name, "A429")
                if sig is None:
                    continue
                # A429 信号名加 word 前缀避免冲突
                sig.name = f"{word_name}.{sig.name}" if word_name else sig.name
                sig.ds_bit_offset += word_bit_offset
                sig.bit_offset = sig.byte_offset * 8 + sig.ds_bit_offset
                self._add_signal(frame, sig)

        if frame.signals:
            member_model.add_frame(frame)
            self.total_messages += 1
            self.protocol_distribution["A429"] += 1

    # ==================== 模拟量处理 ====================

    def _process_analog_port(self, port: ET.Element, member: str, member_model: MemberModel) -> None:
        """处理 AnalogPort (模拟量参数)"""
        port_name = port.get("Name", "").strip()
        for param in list(port.findall("AnalogDiscreteParameter")) + \
                      list(port.findall("AnalogSignalParameter")):
            param_name = param.get("Name", "").strip()
            if not param_name:
                continue
            message_id = f"{member}-{param_name}"
            frame = MessageFrame(
                message_id=message_id,
                member_system=member,
                database_name="",
                frame_name=param_name,
                protocol="ANALOG",
                port_name=port_name,
            )
            for dp in param.findall("DP"):
                sig = self._extract_signal(dp, None, member, param_name, "ANALOG")
                if sig is None:
                    continue
                self._add_signal(frame, sig)
            if frame.signals:
                member_model.add_frame(frame)
                self.total_messages += 1
                self.protocol_distribution["ANALOG"] += 1

    # ==================== 信号添加 (含重名信号保留) ====================

    def _add_signal(self, frame: MessageFrame, sig: SignalDef) -> None:
        """添加信号到消息帧

        航电 ICD 中同一消息的不同 DS 常含同名占位/预留信号 (位于不同字节偏移),
        重名属正常数据冗余, 自动加后缀保留数据完整性, 不视为冲突。
        """
        if sig.name not in frame.signals:
            frame.signals[sig.name] = sig
            self.total_signals += 1
            self._append_entry(frame, sig)
            return

        # 重名: 自动加后缀 (PAD, PAD_2, PAD_3, ...)
        idx = 2
        while f"{sig.name}_{idx}" in frame.signals:
            idx += 1
        sig.name = f"{sig.name}_{idx}"
        frame.signals[sig.name] = sig
        self.total_signals += 1
        self._append_entry(frame, sig)
        # 业务信号重名统计 (结构性信号重名不提示)
        if sig.data_format_type not in STRUCTURAL_DATAFORMATS:
            self.duplicate_signals += 1

    # ==================== 入库条目 ====================

    def _append_entry(self, frame: MessageFrame, sig: SignalDef) -> None:
        """追加信号级入库条目"""
        self.icd_entries.append({
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
            "ata": self._member_ata.get(frame.member_system, ""),
        })

    # ==================== 成员系统处理 ====================

    def _process_member(self, member_elem: ET.Element) -> None:
        """处理单个 MemberSystem 元素"""
        name = member_elem.get("Name", "").strip()
        if not name:
            self.errors.append("MemberSystem 缺少 Name 属性")
            return

        self.total_members += 1
        model = MemberModel(name)
        # 保存成员系统 ATA 章节号 (ATA="21XXXX"/"2601"/"270000" 等 → 取前 2 位)
        ata_raw = member_elem.get("ATA", "").strip()
        self._member_ata[name] = ata_raw[:2] if ata_raw else ""

        # 遍历端口
        for port in list(member_elem):
            port_tag = port.tag
            port_name = port.get("Name", "").strip()
            self.port_distribution[port_tag] += 1
            protocol = PORT_PROTOCOL_MAP.get(port_tag)

            if port_tag == "AnalogPort":
                self._process_analog_port(port, name, model)
                continue

            if protocol is None:
                continue

            if protocol == "A664":
                for msg in port.findall("A664Message"):
                    self._process_a664_message(msg, name, port_name, "A664", model)
            elif protocol == "A825":
                for msg in port.findall("CANMessage"):
                    self._process_can_message(msg, name, port, model)
            elif protocol == "A429":
                for channel in port.findall("A429Channel"):
                    self._process_a429_channel(channel, name, port, model)

        if model.frames:
            self.member_models[name] = model

    # ==================== 主入口 ====================

    def parse(self, file_path: str) -> dict:
        """解析 XML ICD 文件, 返回完整结果"""
        if not os.path.exists(file_path):
            return {"status": "error", "message": f"文件不存在: {file_path}"}

        file_size = os.path.getsize(file_path)
        logger.info(f"开始解析 XML ICD: {os.path.basename(file_path)} ({file_size/1024/1024:.1f} MB)")

        try:
            # 流式解析: 逐 MemberSystem 处理并释放
            for event, elem in ET.iterparse(file_path, events=("end",)):
                if elem.tag == "MemberSystem":
                    self._process_member(elem)
                    elem.clear()
        except ET.ParseError as e:
            self.errors.append(f"XML 解析错误: {e}")
            return {"status": "error", "message": f"XML 解析失败: {e}", "report": self._build_report()}

        logger.info(
            f"XML ICD 解析完成: {self.total_members} 成员, {self.total_messages} 消息, "
            f"{self.total_datasets} DS, {self.total_signals} 信号, "
            f"{len(self.conflicts)} 冲突, {len(self.errors)} 错误")

        return {
            "status": "ok",
            "member_models": self.member_models,
            "icd_entries": self.icd_entries,
            "report": self._build_report(),
        }

    def _build_report(self) -> dict:
        """构建解析结果报告"""
        return {
            "total_members": self.total_members,
            "total_messages": self.total_messages,
            "total_datasets": self.total_datasets,
            "total_signals": self.total_signals,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "warnings": self.warnings,
            "missing_required": self.missing_required,
            "missing_optional": dict(self.missing_optional),
            "vl_reuse_count": self.vl_reuse_count,
            "can_reuse_count": self.can_reuse_count,
            "duplicate_signals": self.duplicate_signals,
            "port_distribution": dict(self.port_distribution),
            "protocol_distribution": dict(self.protocol_distribution),
            "dataformat_distribution": dict(self.dataformat_distribution),
            "ohms_distribution": dict(self.ohms_distribution),
        }


def parse_member_system_xml(file_path: str) -> dict:
    """便捷函数: 解析 Member_System XML ICD 文件"""
    parser = XmlIcdParser()
    return parser.parse(file_path)
