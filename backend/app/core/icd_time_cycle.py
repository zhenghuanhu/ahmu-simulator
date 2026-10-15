"""
Time_Cycle_Status (生命周期数据) ICD 消息编解码
================================================

对齐《Member_System_all_sender_icd_v6_1_1.xml》中飞控模块的生命周期数据定义:

    A664Message Name               FCS_OutputData_TO_OMS_BLK_5
    NameDef                        HF_FLIGHT_CONTROL_MODULE_CLASS.FCS_OutputData_TO_OMS_BLK_5
    VLID                           21162
    MessageSize                    28 (字节)
    MsgDataProtocolType            Parametric data
    TransmissionIntervalMinimum    400 (ms)

28 字节载荷布局 (大端):

    字节  0~3    消息头 (保留, 未在 DS 中定义)
    字节  4~7    FSS_1 帧同步序列 (A664_FSB, Mock 填充 0)
    字节  8~27   DS1 (DataSetSize=20):
                   Time_Cycle_Status  BLK 160位 (BitOffsetWithinDS=0, ByteOffsetWithinMsg=8)

Time_Cycle_Status 为 BLK 块 (160 位 = 20 字节), 内部字段定义引用
E-F227IC015 chapter9, 本 Mock 环境按航电惯例拆分为:

    字节  8~15   Power_On_Time       UBNR 64位  加电时间/经过时间 (单位: 秒)
    字节 16~19   Power_Cycle_Count   UINT 32位  上电循环计数
    字节 20~27   Spare               保留位 (填充 0)

说明:
    - 字节序为大端 (BitOffsetWithinDS=0 对应 DS 首字节, 即最低地址)。
    - FSS 帧同步字节由 AFDX 硬件/协议栈生成, Mock 环境填充 0。
    - 生命周期数据通过 A664 (AFDX) 消息 FCS_OutputData_TO_OMS_BLK_5 周期上报,
      由 AHMU 在维护模式下发出"生命周期获取指令"触发。
"""
import struct


# ICD 消息级元数据 (对齐 Member_System_all_sender_icd_v6_1_1.xml)
TIME_CYCLE_MSG = {
    "name": "FCS_OutputData_TO_OMS_BLK_5",
    "name_def": "HF_FLIGHT_CONTROL_MODULE_CLASS.FCS_OutputData_TO_OMS_BLK_5",
    "member_system": "HF_FCM_1",    # 飞控模块-1 (NameDef=HF_FLIGHT_CONTROL_MODULE_CLASS)
    "vl_id": 21162,           # VLID
    "size": 28,               # MessageSize (字节)
    "period_ms": 400,         # TransmissionIntervalMinimum
    "port": 31696,            # DstUDP (数据加载.yml 中 VL 21162 的 AFDX 端口)
    "port_name": "HFQ_OUT_14_Port_OMS_BLK_5",
    "protocol": "Parametric data",
    "ds_name": "DS1",
    "signal": "Time_Cycle_Status",
}

# Time_Cycle_Status 块: 160 位 = 20 字节, 位于消息字节 8~27
TIMECYCLE_BLOCK_OFFSET = 8        # 消息内字节偏移 (DS1.ByteOffsetWithinMsg=8)
TIMECYCLE_BLOCK_SIZE = 20         # 160 位 = 20 字节

# 块内字段偏移 (相对 Time_Cycle_Status 块起始)
OFFSET_POWER_ON_TIME = 0          # UBNR 64 位 (8 字节), 加电时间(秒)
OFFSET_POWER_CYCLE_COUNT = 8      # UINT 32 位 (4 字节), 上电循环计数
OFFSET_SPARE = 12                 # 保留 8 字节


def pack_time_cycle_status(power_on_time: int, power_cycle_count: int) -> bytes:
    """
    将生命周期数据编码为 20 字节 Time_Cycle_Status 块 (对齐 ICD 160 位 BLK 定义)

    power_on_time:     上电运行时间 (秒)
    power_cycle_count: 上电循环计数
    """
    buf = bytearray(TIMECYCLE_BLOCK_SIZE)  # 20 字节全 0

    # Power_On_Time @ 0~7 (UBNR 64 位大端)
    struct.pack_into(">Q", buf, OFFSET_POWER_ON_TIME, int(power_on_time) & 0xFFFFFFFFFFFFFFFF)

    # Power_Cycle_Count @ 8~11 (UINT 32 位大端)
    struct.pack_into(">I", buf, OFFSET_POWER_CYCLE_COUNT, int(power_cycle_count) & 0xFFFFFFFF)

    # Spare @ 12~19 保留 (保持 0)
    return bytes(buf)


def unpack_time_cycle_status(data: bytes) -> dict:
    """解析 20 字节 Time_Cycle_Status 块为生命周期字段字典"""
    if len(data) < TIMECYCLE_BLOCK_SIZE:
        raise ValueError(f"Time_Cycle_Status 块长度不足: {len(data)} < {TIMECYCLE_BLOCK_SIZE}")
    buf = data[:TIMECYCLE_BLOCK_SIZE]

    power_on_time = struct.unpack_from(">Q", buf, OFFSET_POWER_ON_TIME)[0]
    power_cycle_count = struct.unpack_from(">I", buf, OFFSET_POWER_CYCLE_COUNT)[0]

    return {
        "power_on_time": power_on_time,
        "power_cycle_count": power_cycle_count,
        "spare": buf[OFFSET_SPARE:].hex(),
    }


def pack_time_cycle_message(power_on_time: int, power_cycle_count: int) -> bytes:
    """
    将生命周期数据编码为完整 28 字节 FCS_OutputData_TO_OMS_BLK_5 消息
    (消息头 4 + FSS_1 4 + Time_Cycle_Status 20)
    """
    buf = bytearray(TIME_CYCLE_MSG["size"])  # 28 字节全 0 (消息头/FSS 默认 0)
    block = pack_time_cycle_status(power_on_time, power_cycle_count)
    buf[TIMECYCLE_BLOCK_OFFSET:TIMECYCLE_BLOCK_OFFSET + TIMECYCLE_BLOCK_SIZE] = block
    return bytes(buf)


def unpack_time_cycle_message(data: bytes) -> dict:
    """解析完整 28 字节消息, 返回生命周期字段 + 消息元数据"""
    if len(data) < TIME_CYCLE_MSG["size"]:
        raise ValueError(f"消息长度不足: {len(data)} < {TIME_CYCLE_MSG['size']}")
    block = data[TIMECYCLE_BLOCK_OFFSET:TIMECYCLE_BLOCK_OFFSET + TIMECYCLE_BLOCK_SIZE]
    result = unpack_time_cycle_status(block)
    result.update({
        "message_name": TIME_CYCLE_MSG["name"],
        "vl_id": TIME_CYCLE_MSG["vl_id"],
        "message_size": TIME_CYCLE_MSG["size"],
    })
    return result


def format_power_on_time(seconds: int) -> str:
    """将加电时间(秒)格式化为 HH:MM:SS"""
    if seconds <= 0:
        return "00:00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
