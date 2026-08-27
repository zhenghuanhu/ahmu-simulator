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
