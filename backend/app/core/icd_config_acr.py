"""
ACR (Aircraft Configuration Report, 飞机构型报告) 协议常量与目录
对齐 OHMS test_case/构型信息.py + libs/config_libs/config_verify.py 的接口契约

测试端经 HTTP 调用 6 个 ACR*Request 接口查询构型数据, 响应为 list 包裹 + 特定嵌套字段:
  - ACRATAsRequest      -> [ { "acrAtaStructList": [ {ata, ataName}, ... ] } ]
  - ACREquipsRequest    -> [ { "totalCount": N, "equipNameList": [ {equipName}, ... ] } ]
  - ACRHardInfoRequest  -> [ { colour, state, hardPartNumber, hardPartDescription,
                               hardSerialNumber, hardModificationStatus } ]
  - ACRSpecificDataRequest -> [ { "specificInfoList": [ {colour, specificData}, ... ] } ]
  - ACRSoftInfoRequest  -> [ { colour, softLocationID, softLocationDescription } ]
  - ACRSoftPartResponse -> [ { "acrSoftPartStructList": [ {colour, softPartNumber,
                               softPartDescription}, ... ] } ]

messageID 对照 (config_verify.py):
  0x0301 = ACRATAsRequest
  0x0302 = ACREquipsRequest (测试端亦见 ACREquipmentNameRequest)
  0x0303 = ACRHardInfoRequest
  0x0304 = ACRSpecificDataRequest (测试端亦见 ACRSpecificInfoRequest)
  0x0305 = ACRSoftInfoRequest
  0x0306 = ACRSoftPartResponse (测试端亦见 ACRSoftPartRequest)
"""

# ==================== colour 构型一致性状态 ====================
# 1=正常(与基本构型一致), 2=黄色(构型变更), 3=红色(构型不匹配)
COLOUR_NORMAL = 1
COLOUR_CHANGED = 2
COLOUR_MISMATCH = 3

# ==================== state 构型状态 ====================
# 测试端 expect_hardInfo["state"] = CONFIG_STATE_CURRENT (当前构型)
CONFIG_STATE_CURRENT = "Current"   # 当前构型
CONFIG_STATE_BASIC = "Basic"       # 基本构型

# ==================== ATA 章节名称映射 (ATA 章节号 -> 英文名) ====================
# 覆盖 MEMBER_SYSTEMS 中出现的 ATA 章节; 对齐 ATA100 规范章节命名
ATA_NAMES = {
    "21": "AIR CONDITIONING",
    "22": "AUTO FLIGHT",
    "23": "COMMUNICATIONS",
    "24": "ELECTRICAL POWER",
    "26": "FIRE PROTECTION",
    "27": "FLIGHT CONTROLS",
    "28": "FUEL",
    "30": "ICE AND RAIN PROTECTION",
    "31": "INDICATING/RECORDING SYSTEMS",
    "32": "LANDING GEAR",
    "34": "NAVIGATION",
    "42": "INTEGRATED MODULAR AVIONICS",
    "45": "ONBOARD MAINTENANCE SYSTEM",
    "49": "AIRBORNE AUXILIARY POWER",
    "71": "POWER PLANT",
    "73": "ENGINE FUEL AND CONTROL",
    "80": "STARTING",
}

# AHMU 自身归属的 ATA 章节 (测试端 verify_Display_Item 将其列为 "ONBOARD MAINTENANCE SYSTEM")
AHMU_ATA = "45"


def ata_name(ata: str) -> str:
    """ATA 章节号 -> 英文名 (未知章节回退为 ATA 章节号)"""
    key = str(ata).strip().lstrip("ata").lstrip("ATA")
    return ATA_NAMES.get(key, f"ATA {key}")


# ==================== 四类构型字段生成 (确定性, 基于成员系统标识) ====================
def gen_hard_info(code: str, name: str) -> dict:
    """生成硬件信息 (硬PartNumber/描述/序列号/修改状态)"""
    return {
        "hardPartNumber": f"{code}-100-01",
        "hardPartDescription": name,
        "hardSerialNumber": f"SN-{code}-0001",
        "hardModificationStatus": "01",
    }


def gen_specific_data(code: str) -> list[str]:
    """生成特定数据 (可多条)"""
    return [f"{code}-SPEC-{i:02d}" for i in range(1, 4)]


def gen_soft_info(code: str, name: str) -> dict:
    """生成软件信息 (软LocationID/描述)"""
    return {
        "softLocationID": code,
        "softLocationDescription": f"{name} Software Location",
    }


def gen_soft_parts(code: str) -> list[dict]:
    """生成软件部件列表 (软PartNumber/描述, 可多条)"""
    return [
        {"softPartNumber": f"{code}-SW-{i:02d}",
         "softPartDescription": f"{code} Software Part {i:02d}"}
        for i in range(1, 4)
    ]


# ==================== 构型报告 JSON 编解码 (Mock AFDX, 对齐四类字段) ====================
import json


def encode_config_report(member_system: str, equip_id: int,
                         hard: dict = None, specific: list = None,
                         soft: dict = None, soft_parts: list = None) -> bytes:
    """构造构型报告 JSON 报文 -> bytes (Mock AFDX 载荷)"""
    msg = {
        "member_system": member_system,
        "equip_id": int(equip_id),
        "hard": hard or {},
        "specific": specific or [],
        "soft": soft or {},
        "soft_parts": soft_parts or [],
    }
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")


def decode_config_report(data: bytes):
    """解析构型报告 JSON 报文 -> dict 或 None"""
    try:
        msg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg


def normalize_config_report(msg: dict) -> dict:
    """归一化构型报告: 提取/规整四类字段, 缺省填空"""
    hard = msg.get("hard") or {}
    soft = msg.get("soft") or {}
    specific = msg.get("specific") or msg.get("specificData") or []
    soft_parts = msg.get("soft_parts") or msg.get("softParts") or []
    if isinstance(specific, str):
        specific = [specific]
    if not isinstance(soft_parts, list):
        soft_parts = []
    return {
        "member_system": str(msg.get("member_system") or "").strip(),
        "equip_id": msg.get("equip_id") or msg.get("equipID"),
        "hard_part_number": str(hard.get("hardPartNumber") or ""),
        "hard_part_description": str(hard.get("hardPartDescription") or ""),
        "hard_serial_number": str(hard.get("hardSerialNumber") or ""),
        "hard_modification_status": str(hard.get("hardModificationStatus") or ""),
        "soft_location_id": str(soft.get("softLocationID") or ""),
        "soft_location_description": str(soft.get("softLocationDescription") or ""),
        "specific_data": list(specific),
        "soft_parts": soft_parts,
    }


def validate_config_report(norm: dict) -> str:
    """校验构型报告, 返回错误信息 (空串表示合法)"""
    if not norm["member_system"]:
        return "构型报告缺少 member_system (成员系统标识)"
    return ""


# ==================== ACR 请求编解码 (对齐 ACR_Port.py / 构型参数发送.py) ====================
# 测试端经 AFDX 发送 ACR 构型请求, 按 Deal_Type 区分通道:
#   Deal_Type=1 -> A429 通道, 请求内容 "MSID:xxx:HWPN:AHWSN:A"
#   Deal_Type=2 -> A664 通道, TLV 编码 (MSID + 硬件/软件/附加数据)
# Mock 环境用 UDP 承载 JSON, 语义对齐 (MSID 为请求关键字段, HWPN/AHWSN 为硬件件号/序列号)。

DEAL_TYPE_A429 = 1          # A429 通道
DEAL_TYPE_A664 = 2          # A664 通道
DEAL_TYPE_NAMES = {DEAL_TYPE_A429: "A429", DEAL_TYPE_A664: "A664"}

# ACR 请求字段 (对齐 ACR_Port.py 的 MSID/HWPN/AHWSN 语义)
ACR_REQUEST_FIELDS = ("deal_type", "msid", "hwpn", "ahwsn", "action")


def encode_acr_request(deal_type: int, msid: str, hwpn: str = "",
                       ahwsn: str = "", action: str = "A") -> bytes:
    """构造 ACR 请求 JSON 报文 -> bytes (Mock AFDX 载荷)"""
    msg = {
        "deal_type": int(deal_type),
        "msid": msid,
        "hwpn": hwpn,
        "ahwsn": ahwsn,
        "action": action,
    }
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")


def decode_acr_request(data: bytes) -> dict:
    """解析 ACR 请求 JSON 报文 -> dict 或 None"""
    return decode_config_report(data)  # 复用 JSON 解析


def normalize_acr_request(msg: dict) -> dict:
    """归一化 ACR 请求: 提取 deal_type/msid/hwpn/ahwsn/action"""
    return {
        "deal_type": int(msg.get("deal_type") or DEAL_TYPE_A664),
        "msid": str(msg.get("msid") or msg.get("member_system") or "").strip(),
        "hwpn": str(msg.get("hwpn") or msg.get("hardPartNumber") or ""),
        "ahwsn": str(msg.get("ahwsn") or msg.get("hardSerialNumber") or ""),
        "action": str(msg.get("action") or "A"),
    }


def encode_acr_response(member_system: str, deal_type: int, hard: dict,
                        specific: list, soft: dict, soft_parts: list) -> bytes:
    """构造 ACR 响应 JSON 报文 -> bytes (回传构型数据)"""
    msg = {
        "status": "ok",
        "member_system": member_system,
        "deal_type": int(deal_type),
        "hard": hard,
        "specific": specific,
        "soft": soft,
        "soft_parts": soft_parts,
    }
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")
