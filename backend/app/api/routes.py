"""
API路由层 - REST API + WebSocket端点
"""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, Path
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import json

from app.database import get_db
from app.core.websocket_manager import ws_manager
from app.services.fault_diagnosis import fault_service
from app.services.param_monitor import param_service
from app.services.config_management import config_service
from app.services.startup_test import startup_test_service
from app.services.data_load import data_load_service
from app.services.maintenance_mode import maintenance_service
from app.services.lifecycle import lifecycle_service
from app.services.acars import acars_service
from app.services.print_mgr import print_service
from app.services.engine_trim import engine_trim_service
from app.services.nvm_reset import nvm_reset_service
from app.services.nvm_download import nvm_download_service
from app.services.aircraft_status import aircraft_status_service
from app.core.icd_parser import icd_parser
from app.core.arinc_mock import hardware
from app.config import SIMULATION_CONFIG

router = APIRouter(prefix="/api/v1")


# ==================== 认证 ====================

@router.post("/auth/login")
async def login(username: str = "", password: str = ""):
    """用户登录"""
    from app.config import DEFAULT_USER
    if username == DEFAULT_USER["username"] and password == DEFAULT_USER["password"]:
        return {"status": "ok", "token": "ahmu_sim_token", "user": username}
    return {"status": "error", "message": "用户名或密码错误"}


# ==================== 系统状态 ====================

@router.get("/system/status")
async def get_system_status():
    """获取系统状态"""
    return {
        "mode": maintenance_service.get_mode_info(),
        "hardware_initialized": hardware.is_initialized,
        "icd_loaded": icd_parser.is_loaded,
        "member_count": len(icd_parser.get_all_members()) if icd_parser.is_loaded else 0,
        "total_faults": fault_service.total_faults,
        "active_loads": data_load_service.active_load_count,
        "acars_link": acars_service.link_status,
        "printer_status": print_service.current_printer_status,
        "ws_clients": ws_manager.connection_count,
        "simulation_config": SIMULATION_CONFIG,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/system/mode")
async def get_mode():
    """获取当前模式"""
    return maintenance_service.get_mode_info()


@router.post("/system/maintenance-switch")
async def set_maintenance_switch(position: str = "ground_test"):
    """设置维护开关位置 (ground_test / normal / data_load)
    切换到 ground_test 后, 满足条件持续30s自动进入维护模式
    """
    result = maintenance_service.set_switch(position)
    await ws_manager.broadcast("switch_changed", {
        "switch": position,
        "timestamp": datetime.utcnow().isoformat(),
    })
    return result


@router.post("/system/force-mode")
async def force_system_mode(mode: str = "maintenance"):
    """强制切换系统模式 (测试用)"""
    result = maintenance_service.force_mode(mode)
    await ws_manager.broadcast("mode_change", {
        "mode": mode,
        "previous": result.get("previous"),
        "timestamp": datetime.utcnow().isoformat(),
    })
    return result


@router.get("/system/signals")
async def get_signals():
    """获取当前模拟ARINC664离散量信号状态 (含模式判定条件明细与30s计时进度)"""
    return maintenance_service.get_mode_info()


@router.post("/system/signals")
async def set_signals(payload: dict):
    """设置模拟信号 (模拟RDCU转换后的ARINC664离散量消息)

    支持测试用例信号名 (与机载航电资料一致):
    - Switch_Ground_Test / Switch_Data_Load / Switch_Normal: 维护开关 (互斥)
    - All_Gear_WOW: 轮载信号 (True=地, False=空)
    - Voted_Calibrated_Airspeed: 校准空速 (kts)
    - switch_valid / wow_valid / airspeed_valid: 信号有效性

    示例: {"Switch_Data_Load": true, "All_Gear_WOW": true, "Voted_Calibrated_Airspeed": 80}
    """
    result = maintenance_service.set_signals(payload)
    await ws_manager.broadcast("signals_changed", {
        "signals": maintenance_service.signals,
        "result": result,
        "timestamp": datetime.utcnow().isoformat(),
    })
    return result


# ==================== 故障诊断 ====================

@router.get("/fault/reports")
async def get_fault_reports(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    member: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取故障报告列表"""
    return fault_service.get_fault_list(db, page, size, member, status)


@router.get("/fault/history/{segment}")
async def get_fault_history(
    segment: int = Path(..., ge=-128, le=127),
    db: Session = Depends(get_db),
):
    """历史故障查询 (按航段)"""
    return {"segment": segment, "items": fault_service.get_fault_history(db, segment)}


@router.post("/fault/simulate")
async def simulate_fault(member: str, code: int, severity: str = "minor"):
    """手动模拟故障 (测试用)"""
    return await fault_service.process_fault_report(member, code, severity)


@router.post("/fault/{fault_id}/resolve")
async def resolve_fault(fault_id: str, db: Session = Depends(get_db)):
    """解决故障"""
    if fault_service.resolve_fault(db, fault_id):
        await ws_manager.broadcast("fault_resolved", {
            "fault_id": fault_id, "timestamp": datetime.utcnow().isoformat()
        })
        return {"status": "ok"}
    return {"status": "error", "message": "故障不存在"}


# ==================== 参数监控 ====================

@router.get("/params/list")
async def get_param_list(ata: Optional[str] = None, db: Session = Depends(get_db)):
    """获取参数列表"""
    return {"items": param_service.get_param_list(db, ata)}


@router.get("/params/history/{name}")
async def get_param_history(name: str, limit: int = 100, db: Session = Depends(get_db)):
    """获取参数历史"""
    return {"name": name, "items": param_service.get_param_history(db, name, limit)}


@router.get("/params/quicklists")
async def get_quick_lists(db: Session = Depends(get_db)):
    """获取快捷访问列表"""
    return {"items": param_service.get_quick_lists(db)}


@router.post("/params/quicklists")
async def create_quick_list(name: str, params: list[str], db: Session = Depends(get_db)):
    """创建快捷访问列表"""
    list_id = param_service.create_quick_list(db, name, params)
    return {"status": "ok", "list_id": list_id}


# ==================== 启动测试 ====================

@router.post("/groundtest/start")
async def start_ground_test(member: str, test_type: str = "interactive"):
    """发起启动测试"""
    return await startup_test_service.start_test(member, test_type)


@router.post("/groundtest/{test_id}/ack")
async def send_test_ack(test_id: str):
    """发送ACK确认"""
    return await startup_test_service.send_ack(test_id)


@router.get("/groundtest/list")
async def get_test_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    member: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取测试记录"""
    return startup_test_service.get_test_list(db, member, page, size)


# ==================== 数据加载 ====================

@router.post("/dataload/start")
async def start_data_load(member: str, file: str = "firmware.bin"):
    """发起数据加载"""
    return await data_load_service.start_load(member, file)


@router.get("/dataload/list")
async def get_load_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    member: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取加载任务列表"""
    return data_load_service.get_load_tasks(db, member, page, size)


# ==================== 构型管理 ====================

@router.get("/config/reports")
async def get_config_reports(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    member: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取构型报告"""
    return config_service.get_config_report(db, member, page, size)


@router.post("/config/batch-verify")
async def batch_verify_config(count: int = 400, db: Session = Depends(get_db)):
    """批量构型验证"""
    return config_service.batch_verify(db, count)


# ==================== 生命周期 (成员系统生命周期数据) ====================

@router.get("/lifecycle/logs")
async def get_lifecycle_logs(
    status: Optional[str] = None,
    equip_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """查询生命周期获取日志 (操作时间/操作用户/被操作的成员系统/操作状态)"""
    return lifecycle_service.get_retrieval_logs(db, status, equip_id, page, size)


@router.get("/lifecycle/members")
async def get_lifecycle_members():
    """获取成员系统列表 (含各自生命周期数据), 用于前端成员系统下拉选择"""
    return lifecycle_service.get_member_systems()


@router.post("/lifecycle/member-retrieve/{member}")
async def retrieve_member_lifecycle(member: str, operator: str = "TEST"):
    """查看成员系统生命周期信息: 向成员系统发出生命周期获取指令, 并返回其生命周期信息"""
    return await lifecycle_service.retrieve_member(member, operator)


class LifecycleSaveRequest(BaseModel):
    storage_path: Optional[str] = None   # 用户选择的存储目录 (绝对路径)
    member_system: Optional[str] = None  # 可选, 仅保存指定成员系统


@router.post("/lifecycle/save")
async def save_lifecycle_data(req: LifecycleSaveRequest):
    """主动存储: 将采集到的成员系统生命周期数据保存到用户选择路径"""
    return lifecycle_service.save_lifecycle_data(req.storage_path, req.member_system)


# ==================== ICD管理 ====================

@router.post("/icd/import")
async def import_icd(file_path: str):
    """导入ICD文件"""
    result = icd_parser.parse_icd_file(file_path)
    return {"status": "ok", "result": result}


@router.post("/icd/generate-demo")
async def generate_demo_icd(output_path: str = "", member_count: int = 20):
    """生成演示ICD文件"""
    if not output_path:
        from app.config import CACHE_DIR
        output_path = str(CACHE_DIR / "demo_icd.json")
    icd_parser.generate_demo_icd(output_path, member_count)
    return {"status": "ok", "file_path": output_path}


@router.get("/icd/members")
async def get_icd_members():
    """获取ICD成员系统列表"""
    return {"members": icd_parser.get_all_members(), "is_loaded": icd_parser.is_loaded}


# ==================== ACARS ====================

@router.post("/acars/send")
async def send_acars(message_type: str, content: str, priority: int = 1):
    """发送ACARS消息"""
    return await acars_service.send_downlink(message_type, content, priority)


@router.get("/acars/messages")
async def get_acars_messages(
    direction: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取ACARS消息列表"""
    return acars_service.get_messages(db, direction, page, size)


# ==================== 打印管理 ====================

@router.post("/print/submit")
async def submit_print(content: str, job_type: str = "file_transfer"):
    """提交打印任务"""
    return await print_service.submit_print(content, job_type)


@router.get("/print/jobs")
async def get_print_jobs(page: int = 1, size: int = 20, db: Session = Depends(get_db)):
    """获取打印任务列表"""
    return print_service.get_print_jobs(db, page, size)


# ==================== 发动机配平 (4.3.9) ====================

@router.post("/engine-trim/command")
async def submit_trim_command(payload: dict):
    """下发发动机配平指令 (地面HMI/驾驶舱/PMAT 三来源统一入口)

    请求体示例:
      {"engine_id": 1, "trim_type": "thrust", "target_trim": 0.5,
       "source": "ground", "source_terminal": "GROUND_HMI_01", "operator": "TEST"}
    """
    return await engine_trim_service.submit_trim_command(payload)


@router.get("/engine-trim/commands")
async def get_trim_commands(
    engine_id: Optional[int] = None,
    source: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """查询配平指令列表 (可按发动机/来源筛选)"""
    return engine_trim_service.get_commands(db, engine_id, source, page, size)


@router.get("/engine-trim/data")
async def get_trim_data(engine_id: Optional[int] = None):
    """查询发动机配平数据 (实时, 来自EMU)"""
    return engine_trim_service.get_latest_data(engine_id)


@router.get("/engine-trim/status")
async def get_trim_status():
    """查询配平功能整体状态"""
    return engine_trim_service.get_trim_status()


# ==================== 数据重置管理 (4.3.10) ====================

@router.get("/nvm-reset/members")
async def get_nvm_reset_members():
    """查询启用 NVM 重置服务的成员系统列表"""
    return nvm_reset_service.get_enabled_members()


@router.post("/nvm-reset/reload-config")
async def reload_nvm_reset_config():
    """重新加载 NVM 重置配置文件"""
    return nvm_reset_service.reload_config()


@router.post("/nvm-reset/reset")
async def reset_member_system(payload: dict):
    """发起单个成员系统 NVM 数据重置 (仅维护模式)

    请求体示例:
      {"member_system": "MEM001", "reset_type": "full", "operator": "TEST"}
    """
    member_system = payload.get("member_system", "")
    reset_type = payload.get("reset_type", "full")
    operator = payload.get("operator", "TEST")
    return await nvm_reset_service.reset_member_system(
        member_system, reset_type, operator)


@router.post("/nvm-reset/batch-reset")
async def batch_reset_member_systems(payload: dict):
    """批量重置多个成员系统 NVM 数据 (仅维护模式)

    请求体示例:
      {"member_systems": ["MEM001", "MEM002"], "reset_type": "full", "operator": "TEST"}
    """
    member_systems = payload.get("member_systems", [])
    reset_type = payload.get("reset_type", "full")
    operator = payload.get("operator", "TEST")
    return await nvm_reset_service.batch_reset(member_systems, reset_type, operator)


@router.get("/nvm-reset/logs")
async def get_nvm_reset_logs(
    member_system: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """查询 NVM 重置日志 (可按成员系统/状态筛选)"""
    return nvm_reset_service.get_reset_logs(db, member_system, status, page, size)


@router.get("/nvm-reset/results")
async def get_nvm_reset_results(
    reset_id: Optional[str] = None,
    member_system: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """查询 NVM 重置结果"""
    return nvm_reset_service.get_reset_results(db, reset_id, member_system, page, size)


@router.post("/nvm-reset/print/{reset_id}")
async def print_nvm_reset_result(reset_id: str):
    """打印 NVM 重置操作结果 (发送至信息系统打印机)"""
    return await nvm_reset_service.print_reset_result(reset_id)


@router.get("/nvm-reset/status")
async def get_nvm_reset_status():
    """查询数据重置管理功能整体状态"""
    return nvm_reset_service.get_reset_status()


# ==================== 数据下载管理 (4.3.14) ====================

@router.post("/nvm-download/retrieve")
async def retrieve_nvm_data(payload: dict):
    """从人机界面接收成员系统 NVM 数据获取指令 (仅维护模式)

    请求体示例:
      {"member_system": "MEM001", "data_type": "fault_snapshot", "operator": "TEST"}
    """
    member_system = payload.get("member_system", "")
    data_type = payload.get("data_type", "fault_snapshot")
    operator = payload.get("operator", "TEST")
    return await nvm_download_service.retrieve_member_system(
        member_system, data_type, operator)


@router.post("/nvm-download/batch-retrieve")
async def batch_retrieve_nvm_data(payload: dict):
    """批量获取多个成员系统 NVM 数据 (仅维护模式)

    请求体示例:
      {"member_systems": ["MEM001", "MEM002"], "data_type": "fault_snapshot", "operator": "TEST"}
    """
    member_systems = payload.get("member_systems", [])
    data_type = payload.get("data_type", "fault_snapshot")
    operator = payload.get("operator", "TEST")
    return await nvm_download_service.batch_retrieve(member_systems, data_type, operator)


@router.get("/nvm-download/logs")
async def get_nvm_download_logs(
    member_system: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """查询 NVM 下载日志 (可按成员系统/状态筛选)"""
    return nvm_download_service.get_download_logs(db, member_system, status, page, size)


@router.get("/nvm-download/data")
async def get_nvm_data_list(
    member_system: Optional[str] = None,
    data_type: Optional[str] = None,
    download_status: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """查询 NVM 数据列表 (数据库管理)"""
    return nvm_download_service.get_nvm_data(
        db, member_system, data_type, download_status, page, size)


@router.post("/nvm-download/export/{nvm_data_id}")
async def export_nvm_data_to_pmat(nvm_data_id: str):
    """将数据库中存储的 NVM 数据下载到 PMAT"""
    return await nvm_download_service.export_to_pmat(nvm_data_id)


@router.post("/nvm-download/print/{download_id}")
async def print_nvm_download_result(download_id: str):
    """打印 NVM 数据获取结果 (发送至信息系统打印机)"""
    return await nvm_download_service.print_download_result(download_id)


@router.get("/nvm-download/status")
async def get_nvm_download_status():
    """查询数据下载管理功能整体状态"""
    return nvm_download_service.get_download_status()


# ==================== 飞机状态消息 (4.3.7) ====================

@router.get("/aircraft-status")
async def get_aircraft_status():
    """查询最新飞机状态消息 (OHMS 阶段/航段/全局终止标志等)"""
    return aircraft_status_service.get_latest_message()


@router.get("/aircraft-status/status")
async def get_aircraft_status_service():
    """查询飞机状态服务整体状态"""
    return aircraft_status_service.get_status()


@router.post("/aircraft-status/signal")
async def set_aircraft_status_signal(payload: dict):
    """注入飞机状态信号 (模拟从起落架/大气/飞管等系统接收信号)

    请求体示例:
      {"source": "left", "param": "airspeed", "value": 250.0, "valid": true}
      {"source": "right", "param": "air_ground_status", "value": "air", "valid": true}
    """
    source = payload.get("source", "left")
    param = payload.get("param", "")
    value = payload.get("value")
    valid = payload.get("valid", True)
    return aircraft_status_service.set_source_signal(source, param, value, valid)


@router.post("/aircraft-status/identity")
async def set_aircraft_identity(payload: dict):
    """设置飞机身份信息 (由飞管系统/ATC提供: ICAO码/注册号/航班号/机场)"""
    return aircraft_status_service.set_aircraft_identity(payload)
