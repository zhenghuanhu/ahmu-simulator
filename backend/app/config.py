"""
AHMU 仿真器 - 全局配置
适配 Windows 本地开发测试环境
"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"
CONFIG_DIR = BASE_DIR / "config"
LOG_DIR = BASE_DIR / "logs"

# 确保目录存在
for d in [DATA_DIR, CACHE_DIR, CONFIG_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 数据库配置
DATABASE_PATH = DATA_DIR / "ahmu.db"
DATABASE_URL = f"sqlite:///{DATABASE_URL_PATH(DATABASE_PATH)}" if False else f"sqlite:///{DATABASE_PATH}"

def DATABASE_URL_PATH(p):
    return str(p).replace("\\", "/")

DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

# WebSocket 配置
WS_PATH = "/ws/ahmu"
MAX_WS_CONNECTIONS = 5  # 支持5个终端同时接入

# 服务器配置
HOST = "0.0.0.0"
PORT = 8443
DEBUG = True
RELOAD = True

# 仿真参数
SIMULATION_CONFIG = {
    "member_system_count": 500,       # 成员系统数量
    "fault_report_max": 50000,       # 最大故障报告数
    "failure_report_max": 25000,     # 最大失效报告数
    "fault_per_segment": 2500,       # 每航段故障数
    "segment_count": 512,            # 航段数
    "data_send_period_hz": 1,        # 数据收发频率(Hz)
    "max_concurrent_load": 3,        # 最大同时加载数
    "max_terminals": 5,              # 最大终端接入数
}

# 故障诊断功能配置 (4.3.6 故障诊断)
FAULT_DIAGNOSIS_CONFIG = {
    # 是否启用成员系统周期自动上报故障。
    # True  = 成员系统按 report_frequencies 周期自发上报故障 (持续产生故障报告)
    # False = 关闭自动上报, 故障仅通过 ICD 故障注入(接口/界面"注入故障")手动产生 (默认, 可控)
    "auto_report_enabled": False,
    # 每个成员系统每次上报周期实际产生故障的概率 (0~1), 仅 auto_report_enabled=True 时生效
    "auto_report_probability": 0.005,
    # 成员系统故障上报频率 (Hz)
    "report_frequencies": [0.25, 0.5, 1.0, 2.0],
}

# 发动机配平功能配置 (4.3.9 发动机配平)
ENGINE_TRIM_CONFIG = {
    "engine_count": 4,                              # 发动机数量 (四发飞机)
    "trim_range": [-5.0, 5.0],                      # 配平值范围 (%)
    "trim_types": ["thrust", "power", "fuel_flow"], # 配平类型: 推力/功率/燃油流量
    "command_sources": ["ground", "cockpit", "pmat"],  # 指令来源: 地面HMI/驾驶舱/PMAT
    "ack_timeout_sec": 5,                           # EMU 指令接受确认超时 (秒)
    "trim_apply_timeout_sec": 30,                   # 配平执行到位超时 (EMU 从接受到 applied 最长时间)
    "max_concurrent_per_engine": 1,                 # 每台发动机最大并发配平指令数
    "data_report_period_ms": 1000,                  # EMU 配平数据上报周期 (毫秒)
}

# 发动机监视装置 (EMU) Mock 配置
ENGINE_MONITOR_CONFIG = {
    "engine_count": 4,
    "n1_range": [20.0, 105.0],          # N1 低压转子转速 (%)
    "n2_range": [20.0, 105.0],          # N2 高压转子转速 (%)
    "egt_range": [200.0, 1000.0],       # 排气温度 (℃)
    "fuel_flow_range": [300.0, 6000.0], # 燃油流量 (kg/h)
    "thrust_rating_range": [20.0, 110.0],  # 推力额定 (%)
    "a664_vl_base": 21,                 # 发动机监视数据 A664 虚拟链路起始号 (VL21~VL24, 避开 VL1~10)
    "emu_validity_fail_prob": 0.03,     # EMU 数据失效模拟概率
}

# 数据重置管理功能配置 (4.3.10 数据重置管理)
# 需求: 通过读取配置文件确定启用 NVM 重置服务的成员系统列表;
#       仅能在维护模式下重置成员系统 NVM 数据; 接收并存储重置结果; 记录重置日志
#       响应用户打印指令, 将重置结果发送给信息系统打印机
NVM_RESET_CONFIG = {
    "config_file": "nvm_reset_config.json",   # 启用 NVM 重置服务的成员系统配置文件 (位于 CONFIG_DIR)
    "reset_timeout_sec": 10,                  # 成员系统重置响应超时 (秒)
    # 重置类型: full/partial = NVM 数据重置; fault_history = 故障历史重置 (LRU Fault History Reset)
    "reset_types": ["full", "partial", "fault_history"],
    "max_concurrent_resets": 5,               # 最大并发重置数
    "result_success_prob": 1.0,               # Mock 重置成功概率 (1.0=确定性成功, 保证端到端测试稳定)
}

# 打印输出配置 (4.3.10 数据重置管理 - 打印结果输出至电子盘)
# 测试用例: 通过 ACoreIDE 访问电子盘 A:\printlog, 下载打印 ps 格式文件
# 本机无 A 盘, 用 DATA_DIR/printlog 模拟电子盘 A:\printlog
PRINT_CONFIG = {
    "output_dir": "printlog",          # 打印输出目录名 (位于 DATA_DIR 下)
    "file_extension": "ps",            # 打印文件格式 (PostScript)
    "simulated_drive": "A:\\printlog",  # 模拟的电子盘路径 (展示/说明用)
    # 是否启用内置模拟打印功能。
    # True  = 无外部打印机响应时, 由仿真器内置模拟完成打印(生成 ps + 状态 completed), 便于无打印机环境开发联调 (默认)
    # False = 严格依赖外部打印机(真实/模拟打印机经 UDP 协议响应), 无响应则判打印失败 (用于协议符合性验证)
    "simulated_print_enabled": True,
    # ARINC744A 打印协议通道 (Mock UDP, 对齐 ICD Printer_GIPC_Data VL 23247)
    "afdx_vl_id": 23247,               # Printer_GIPC_Data 虚拟链路 VL ID
    "send_port": 34801,                # AHMU -> 打印机 (AFDX 打印数据发送端口)
    "recv_port": 34802,                # 打印机 -> AHMU (A429 ACK/状态接收端口)
    "recv_host": "0.0.0.0",            # 接收监听地址
    "block_size": 150,                 # 每块有效数据大小 (对齐 ETest BLOCK_SIZE)
    "ack_timeout_sec": 3,              # ACK 超时 (秒)
    "max_retry": 3,                    # 最大重发次数
}

# 数据下载管理功能配置 (4.3.14 数据下载管理)
# 需求: 从人机界面接收成员系统NVM数据获取指令; 仅维护模式下允许获取;
#       实时显示获取进度; 存储NVM数据+下载日志; 支持下载到PMAT; 打印获取结果
NVM_DOWNLOAD_CONFIG = {
    "data_types": ["fault_snapshot", "config_snapshot", "life_cycle"],  # NVM 数据类型
    "retrieve_timeout_sec": 15,                  # 获取超时 (秒)
    "max_concurrent_retrieves": 5,               # 最大并发获取数
    "data_size_range": [1024, 4 * 1024 * 1024],  # NVM 数据大小范围 (字节, 1KB~4MB)
    "retrieve_success_prob": 1.0,               # Mock 获取成功概率 (1.0=确定性成功, 保证端到端测试稳定)
    "progress_steps": 20,                        # 进度递增步数 (每步 ~0.1s)
}

# 飞机状态消息功能配置 (4.3.7 飞机状态消息)
# 需求: 1Hz周期性发布OHMS飞机状态消息; 双源(左/右)8参数计算飞行阶段1-15;
#       接收起落架轮载/大气空速/飞管尾号+航班号信号
AIRCRAFT_STATUS_CONFIG = {
    "publish_period_ms": 1000,       # 发布周期 1Hz (1000ms)
    "port_a664": 34896,              # A664 飞机状态消息端口
    "port_a429": 34897,              # A429 飞机状态消息端口
    "port_a825": 0x600,              # A825 飞机状态消息 CAN ID (备份通道)
    # 飞机身份信息 (由其他系统提供: 飞管系统/ATC等)
    "icao_code": "780123",           # ICAO 应答机码 (24bit, 八进制)
    "registration": "B-001A",        # 注册号 (飞机尾号)
    "flight_number": "CXF001",       # 航班号
    "departure_airport": "ZSPD",     # 出发机场 (浦东)
    "destination_airport": "ZBAA",   # 目的地机场 (首都)
    # 飞行阶段判定阈值
    "airspeed_liftoff_kts": 120.0,   # 离地/起飞空速阈值
    "airspeed_taxi_kts": 30.0,       # 滑行空速阈值
    "ground_speed_taxi_kts": 3.0,    # 滑行地速阈值
    "altitude_cruise_ft": 30000.0,   # 巡航高度
    "altitude_approach_ft": 8000.0,  # 进近高度
    "altitude_rate_climb_fpm": 300.0,  # 爬升高度变化率阈值 (ft/min)
    "altitude_rate_cruise_fpm": 200.0, # 巡航高度变化率容差 (ft/min)
    "thrust_takeoff_deg": 40.0,      # 起飞推力油门角度阈值 (度)
    "thrust_idle_deg": 15.0,         # 怠速油门角度阈值 (度)
    # 双源参数有效性: 左源失效时切换到右源
    "dual_source_params": [
        "air_ground_status",           # 空地状态 (起落架)
        "airspeed",                    # 空速 (大气)
        "ground_speed",                # 地速 (惯导/飞管)
        "fcm_corrected_altitude_rate", # FCM修正高度变化率 (飞控)
        "engine_thrust_lever_angle",   # 发动机油门推力角度 (发动机)
        "flight_altitude",             # 飞行高度 (大气)
        "brake_status",                # 刹车状态 (刹车系统)
        "maintenance_switch_position", # 维护开关位置 (驾驶舱控制板)
    ],
}

# AFDX/A664 参数监控接收通道配置 (4.3.12 参数监控 - AFDX 数据链路)
# 真实 AFDX 经虚拟链路(VL)承载; Mock 环境用 UDP 单播模拟, 每个通道监听一个 UDP 端口,
# 收到报文后按 ICD 消息帧定义解码为信号并推送到参数监控。
AFDX_PARAM_CONFIG = {
    "listen_host": "0.0.0.0",   # UDP 监听地址
    "channels": [
        {
            "name": "EEC_EICAS",                   # 通道名
            "member_system": "HF_EEC_L_CHA",       # 成员系统 (ICD 成员名)
            "frame_name": "A664_Msg_EEC_EICAS",    # ICD 消息帧名
            "vl_id": 23275,                         # 虚拟链路 VL ID (来自 ICD)
            "udp_port": 34800,                      # Mock UDP 监听端口
            "enabled": True,
        },
    ],
}

# ACARS 管理功能配置 (4.3.x ACARS 数据链通信)
# 需求: AHMU 仿真器仅实现 AHMU 自身功能——提取内部数据(故障/失效+FDE/构型/事件+参数)
#       -> 整合为结构化维护数据包 -> 封装 A664 帧(载荷=维护数据包) -> 经 A664 总线发送;
#       接收 CPM 转发的上行 EOR 请求与 A664 ACK; 支持配置优先级/下传链路/触发逻辑;
#       监控链路状态(idle/busy/lost)。ARINC619 封装/664↔429 转换/CMU 由下游模拟程序合并实现。
ACARS_CONFIG = {
    "recv_port": 34810,              # AHMU 接收上行 (CPM 发送 EOR 请求/ACK -> AHMU 接收)
    "send_port": 34811,              # AHMU 发送下行 (AHMU 发送 A664 维护数据 -> CPM 接收)
    "recv_host": "0.0.0.0",          # UDP 接收监听地址
    "priority": 1,                   # 默认优先级: 0=low, 1=normal, 2=high
    "downlink_channel": "a664",      # 下传链路: a664(主, AFDX)/a429(备)
    "trigger_mode": "on_request",    # 触发逻辑: on_request(上行请求触发)/periodic(周期)/event(事件触发)
    "ack_timeout_sec": 3,            # A664 ACK 等待超时 (秒)
    "max_retry": 3,                  # 下行发送最大重发次数
    "afdx_vl_id": 20086,             # A653_OUT_DL_CMU_MSG 虚拟链路 VL ID (来自 ICD)
    "eor_list": [
        "DNL_ACMF101/0/EOR", "DNL_ACMF102/0/EOR", "DNL_ACMF103/0/EOR",
        "DNL_ACMF104/0/EOR", "DNL_ACMF105/0/EOR", "DNL_ACMF106/0/EOR",
        "DNL_ACMF201/0/EOR", "DNL_ACMF202/0/EOR",
        "DNL_ACMF301/0/EOR", "DNL_ACMF401/0/EOR",
    ],
}

# 维护模式条件 (三者同时满足且持续超过30s)
#   "空/地"信号=地 (All_Gear_WOW=True) / 空速<80kts / 维护开关=地面测试或数据加载
MAINTENANCE_MODE_CONDITIONS = {
    "all_gear_wow": True,            # All_Gear_WOW (轮载, True=地)
    "voted_calibrated_airspeed": 80,  # 空速低于80kts
    "hold_duration_sec": 30,          # 条件持续≥30s
}

# 正常模式条件 (三者同时满足)
#   "空/地"信号=空 (All_Gear_WOW=False) / 空速>80kts / 维护开关=正常
NORMAL_MODE_CONDITIONS = {
    "air_ground": "air",              # 空/地=空
    "airspeed": 80,                   # 空速大于80kts
    "maintenance_switch": "normal",   # 维护开关=正常
}

# OHMS 界面风格
UI_THEME = {
    "background": "#000000",          # 黑色背景
    "text_color": "#FFFFFF",          # 白色文字
    "highlight_color": "#00FFFF",     # 青色突出显示
    "font_family": "Consolas, Microsoft YaHei, monospace",
}

# 默认用户
DEFAULT_USER = {
    "username": "TEST",
    "password": "123456",
}

# ICD 缓存配置
ICD_CACHE_FILE = CACHE_DIR / "icd_snapshot.msgpack"

# 共享内存配置 (Windows兼容)
SHM_CONFIG = {
    "name": "AHMU_SHM_BUFFER",
    "size": 4 * 1024 * 1024,  # 4MB
    "regions": ["a664", "a429", "control", "status"],
}

# 日志配置
LOG_CONFIG = {
    "rotation": "100 MB",
    "retention": 10,  # 保留10个日志文件, 100MB×10=1GB上限
    "level": "DEBUG" if DEBUG else "INFO",
    "format": "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
}
