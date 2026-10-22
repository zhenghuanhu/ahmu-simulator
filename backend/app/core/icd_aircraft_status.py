"""
Aircraft_Status_Msg (A664/AFDX) ICD 消息编解码
==============================================

对齐《Member_System_all_sender_icd_v6_1_1.xml》中 HF_AHMUINSTANCE 的
Aircraft_Status_Msg 消息字节定义 (权威信号级定义):

    A664Message Name          Aircraft_Status_Msg
    VLID                      23212
    MessageSize               68 (字节)
    MsgDataProtocolType       Parametric data
    TransmissionIntervalMinimum 1000 (ms, 1Hz)

68 字节载荷布局 (大端, 字节偏移 = DS.ByteOffsetWithinMsg + DP.BitOffsetWithinDS/8):

    字节  0~3    消息头 (保留, 未在 DS 中定义)
    字节  4~7    FSS_1 帧同步序列 (FSB_4/3/2/1, A664_FSB)
    字节  8~9    Flight_Leg        SINT 16位  飞行航段 (-128~127)
    字节 10~11   PAD
    字节 12~13   Flight_Phase      UBNR 16位  飞行阶段 1~15 (LsbRes=1)
    字节 14~15   PAD
    字节 16~23   UTC 时间 (UBNR 8位): days/months/years/PAD/secs/mins/hours/PAD
    字节 24~26   ICAO_Code         UINT 24位  应答机码
    字节 27      PAD
    字节 28~31   FSS_2 帧同步序列 (FSB_7/6/5, A664_FSB)
    字节 32~43   Reg_Num           ISO-5      注册号 (9 字符 + String_Length)
    字节 44~63   Flight_Num + 机场  ISO-5      航班号(8) + 出发/目的机场(各4)
    字节 64      Global_Abort_Flag BOOL 8位   全局终止标志 (0/1)
    字节 65~67   PAD

说明:
    - 字节序为大端 (BitOffsetWithinDS=0 对应 DS 首字节, 即最低地址)。
    - FSS 帧同步字节由 AFDX 硬件/协议栈生成, Mock 环境填充 0, 接收侧不校验。
    - ISO-5 字符集与 ASCII 兼容 (7 位)。
    - 身份信息字符顺序在源 ICD 中为倒序交错排列, Mock 环境按逻辑顺序编码
      (注册号/航班号/机场), String_Length 填入实际长度, 保证 pack/unpack 对称。
"""
import struct
from datetime import datetime, timezone

# ICD 消息级元数据 (对齐 EoICD_Publisher_Table_v6_1_1.xlsx)
AIRCRAFT_STATUS_MSG = {
    "name": "Aircraft_Status_Msg",
    "port": 34896,             # Publisher_port
    "vl_id": 23212,            # Publisher_VL_Id
    "size": 68,                # Publisher_MessageSize (字节)
    "period_ms": 1000,         # PublisherPort_RefreshPeriod (1Hz)
    "node": "HF_AHMUINSTANCE",
    "dest_format": "MULTICAST",
    "protocol": "Parametric data",
}

# 关键字段字节偏移 (对齐 XML DS/DP 定义)
OFFSET_FLIGHT_LEG = 8          # SINT 16 位
OFFSET_FLIGHT_PHASE = 12       # UBNR 16 位
OFFSET_UTC = 16                # UBNR 8 位 ×8 (含 2 字节 PAD)
OFFSET_ICAO = 24               # UINT 24 位
OFFSET_REG = 32                # 注册号 12 字节
OFFSET_FLIGHT_AIRPORT = 44     # 航班号+机场 20 字节
OFFSET_ABORT = 64              # BOOL 8 位

# 默认身份信息 (对齐 config.py AIRCRAFT_STATUS_CONFIG)
DEFAULT_IDENTITY = {
    "icao_code": "780123",
    "registration": "B-001A",
    "flight_number": "CXF001",
    "departure_airport": "ZSPD",
    "destination_airport": "ZBAA",
}


def _encode_iso5(value: str, length: int) -> bytes:
    """字符串编码为定长 ISO-5 (ASCII) 字节, 不足补 0, 超出截断"""
    return value.encode("ascii", "ignore")[:length].ljust(length, b"\x00")


def _decode_iso5(data: bytes) -> str:
    """定长 ISO-5 字节解码为字符串 (去尾 0)"""
    return data.decode("ascii", "ignore").rstrip("\x00")


def _parse_icao(value) -> int:
    """将 ICAO 码解析为 24 位整数 (Mode S 地址)

    Aircraft_Status_Msg 的 ICAO_Code 为 UINT 24 位 Mode S 地址,
    规范表示法为 6 位十六进制 (如 0x780123, 与 unpack 的 f"{:06X}" 对应)。
    兼容: int 原样取低 24 位; str 支持 0x/0X 前缀、纯十六进制、纯十进制。
    注意: 不再尝试八进制, 消除「十六进制优先 vs 八进制」的歧义
          (ICAO 应答机 squawk 虽为八进制, 但此处 24 位字段语义是 Mode S 十六进制地址)。
    """
    if isinstance(value, int):
        return value & 0xFFFFFF
    s = str(value).strip()
    if not s:
        return 0
    if s.lower().startswith("0x"):
        try:
            return int(s, 16) & 0xFFFFFF
        except ValueError:
            return 0
    # 优先十六进制 (Mode S 地址标准), 回退十进制 (纯数字字符串)
    try:
        return int(s, 16) & 0xFFFFFF
    except ValueError:
        try:
            return int(s, 10) & 0xFFFFFF
        except ValueError:
            return 0


def _encode_registration(reg: str) -> bytes:
    """注册号 (最多 9 字符) → 12 字节, 按 ICD DS_5 倒序交错布局

    字节 0=Char_2, 1=Char_1, 2~3=String_Length_1, 4=Char_6, 5=Char_5, 6=Char_4,
    7=Char_3, 8=PAD, 9=Char_9, 10=Char_8, 11=Char_7
    """
    c = reg.encode("ascii", "ignore")[:9].ljust(9, b"\x00")
    raw_len = min(len(reg.encode("ascii", "ignore")), 9)
    buf = bytearray(12)
    buf[0] = c[1]   # Reg_Num_Char_2
    buf[1] = c[0]   # Reg_Num_Char_1
    buf[2:4] = raw_len.to_bytes(2, "big")  # String_Length_1
    buf[4] = c[5]   # Reg_Num_Char_6
    buf[5] = c[4]   # Reg_Num_Char_5
    buf[6] = c[3]   # Reg_Num_Char_4
    buf[7] = c[2]   # Reg_Num_Char_3
    buf[8] = 0      # PAD
    buf[9] = c[8]   # Reg_Num_Char_9
    buf[10] = c[7]  # Reg_Num_Char_8
    buf[11] = c[6]  # Reg_Num_Char_7
    return bytes(buf)


def _encode_flight_airport(flight_no: str, dep: str, dest: str) -> bytes:
    """航班号(8) + 出发机场(4) + 目的机场(4) → 20 字节, 按 ICD DS_6 倒序交错布局

    字节 0=Flight_Num_Char_2, 1=Flight_Num_Char_1, 2~3=String_Length_2,
    4~7=Flight_Num_Char_6/5/4/3, 8=Dest_Char_2, 9=Dest_Char_1,
    10=Flight_Num_Char_8, 11=Flight_Num_Char_7, 12=Dep_Char_2, 13=Dep_Char_1,
    14=Dest_Char_4, 15=Dest_Char_3, 16~17=PAD, 18=Dep_Char_4, 19=Dep_Char_3
    """
    fn = flight_no.encode("ascii", "ignore")[:8].ljust(8, b"\x00")
    de = dep.encode("ascii", "ignore")[:4].ljust(4, b"\x00")
    ds = dest.encode("ascii", "ignore")[:4].ljust(4, b"\x00")
    fn_len = min(len(flight_no.encode("ascii", "ignore")), 8)
    buf = bytearray(20)
    buf[0] = fn[1]   # Flight_Num_Char_2
    buf[1] = fn[0]   # Flight_Num_Char_1
    buf[2:4] = fn_len.to_bytes(2, "big")  # String_Length_2
    buf[4] = fn[5]   # Flight_Num_Char_6
    buf[5] = fn[4]   # Flight_Num_Char_5
    buf[6] = fn[3]   # Flight_Num_Char_4
    buf[7] = fn[2]   # Flight_Num_Char_3
    buf[8] = ds[1]   # Destination_Airport_Char_2
    buf[9] = ds[0]   # Destination_Airport_Char_1
    buf[10] = fn[7]  # Flight_Num_Char_8
    buf[11] = fn[6]  # Flight_Num_Char_7
    buf[12] = de[1]  # Departure_Airport_Char_2
    buf[13] = de[0]  # Departure_Airport_Char_1
    buf[14] = ds[3]  # Destination_Airport_Char_4
    buf[15] = ds[2]  # Destination_Airport_Char_3
    buf[16:18] = b"\x00\x00"  # PAD
    buf[18] = de[3]  # Departure_Airport_Char_4
    buf[19] = de[2]  # Departure_Airport_Char_3
    return bytes(buf)


def pack_aircraft_status(flight_phase: int,
                         global_abort_flag: bool = True,
                         flight_leg: int = 0,
                         icao_code=DEFAULT_IDENTITY["icao_code"],
                         registration: str = DEFAULT_IDENTITY["registration"],
                         flight_number: str = DEFAULT_IDENTITY["flight_number"],
                         departure_airport: str = DEFAULT_IDENTITY["departure_airport"],
                         destination_airport: str = DEFAULT_IDENTITY["destination_airport"],
                         utc=None) -> bytes:
    """
    将飞机状态消息编码为 68 字节 A664 参数数据载荷 (对齐权威 ICD)

    utc: None 使用当前 UTC 时间; 或 (days, months, years, hours, minutes, seconds) 元组
    """
    if utc is None:
        now = datetime.now(timezone.utc)
        days, months, years, hours, minutes, seconds = (
            now.day, now.month, now.year % 100, now.hour, now.minute, now.second)
    else:
        days, months, years, hours, minutes, seconds = utc

    buf = bytearray(AIRCRAFT_STATUS_MSG["size"])   # 68 字节全 0 (消息头/FSS/PAD 默认 0)

    # Flight_Leg @ 8~9 (SINT 16 位大端)
    struct.pack_into(">h", buf, OFFSET_FLIGHT_LEG, int(flight_leg))

    # Flight_Phase @ 12~13 (UBNR 16 位大端)
    struct.pack_into(">H", buf, OFFSET_FLIGHT_PHASE, int(flight_phase))

    # UTC 时间 @ 16~22 (UBNR 8 位: days/months/years/PAD/secs/mins/hours/PAD)
    buf[OFFSET_UTC + 0] = days & 0xFF
    buf[OFFSET_UTC + 1] = months & 0xFF
    buf[OFFSET_UTC + 2] = years & 0xFF
    buf[OFFSET_UTC + 4] = seconds & 0xFF
    buf[OFFSET_UTC + 5] = minutes & 0xFF
    buf[OFFSET_UTC + 6] = hours & 0xFF

    # ICAO_Code @ 24~26 (UINT 24 位)
    buf[OFFSET_ICAO:OFFSET_ICAO + 3] = _parse_icao(icao_code).to_bytes(3, "big")

    # 注册号 @ 32~43 (ISO-5)
    buf[OFFSET_REG:OFFSET_REG + 12] = _encode_registration(registration)

    # 航班号 + 机场 @ 44~63 (ISO-5)
    buf[OFFSET_FLIGHT_AIRPORT:OFFSET_FLIGHT_AIRPORT + 20] = _encode_flight_airport(
        flight_number, departure_airport, destination_airport)

    # Global_Abort_Flag @ 64 (BOOL)
    buf[OFFSET_ABORT] = int(bool(global_abort_flag))

    return bytes(buf)


def unpack_aircraft_status(data: bytes) -> dict:
    """解析 68 字节 A664 参数数据载荷为字段字典 (对齐权威 ICD)"""
    if len(data) < AIRCRAFT_STATUS_MSG["size"]:
        raise ValueError(f"消息长度不足: {len(data)} < {AIRCRAFT_STATUS_MSG['size']}")
    buf = data[:AIRCRAFT_STATUS_MSG["size"]]

    flight_leg = struct.unpack_from(">h", buf, OFFSET_FLIGHT_LEG)[0]
    flight_phase = struct.unpack_from(">H", buf, OFFSET_FLIGHT_PHASE)[0]
    global_abort_flag = bool(buf[OFFSET_ABORT])
    icao_code = int.from_bytes(buf[OFFSET_ICAO:OFFSET_ICAO + 3], "big")

    # 注册号 (12 字节, 倒序交错: Char_2/Char_1/StrLen/Char_6/5/4/3/PAD/Char_9/8/7)
    reg_raw = buf[OFFSET_REG:OFFSET_REG + 12]
    reg_len = int.from_bytes(reg_raw[2:4], "big")
    reg_chars = [reg_raw[1], reg_raw[0], reg_raw[7], reg_raw[6], reg_raw[5],
                 reg_raw[4], reg_raw[11], reg_raw[10], reg_raw[9]]
    registration = _decode_iso5(bytes(reg_chars[:min(reg_len, 9)]))

    # 航班号 + 机场 (20 字节, 倒序交错: 见 _encode_flight_airport)
    fa = buf[OFFSET_FLIGHT_AIRPORT:OFFSET_FLIGHT_AIRPORT + 20]
    flg_len = int.from_bytes(fa[2:4], "big")
    fn = [fa[1], fa[0], fa[7], fa[6], fa[5], fa[4], fa[11], fa[10]]
    flight_number = _decode_iso5(bytes(fn[:min(flg_len, 8)]))
    departure_airport = _decode_iso5(bytes([fa[13], fa[12], fa[19], fa[18]]))
    destination_airport = _decode_iso5(bytes([fa[9], fa[8], fa[15], fa[14]]))

    return {
        "flight_phase": flight_phase,
        "global_abort_flag": global_abort_flag,
        "flight_leg": flight_leg,
        "icao_code": f"{icao_code:06X}",
        "registration": registration,
        "flight_number": flight_number,
        "departure_airport": departure_airport,
        "destination_airport": destination_airport,
        "utc": {
            "days": buf[OFFSET_UTC + 0],
            "months": buf[OFFSET_UTC + 1],
            "years": buf[OFFSET_UTC + 2],
            "seconds": buf[OFFSET_UTC + 4],
            "minutes": buf[OFFSET_UTC + 5],
            "hours": buf[OFFSET_UTC + 6],
        },
    }
