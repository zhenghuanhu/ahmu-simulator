"""
API路由层 - REST API + WebSocket端点
"""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, Path
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
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


# ==================== 生命周期 ====================

@router.post("/lifecycle/retrieve/{member}")
async def retrieve_lifecycle(member: str):
    """获取单个成员系统生命周期"""
    return await lifecycle_service.retrieve_lifecycle(member)


@router.post("/lifecycle/batch-retrieve")
async def batch_retrieve_lifecycle(count: int = 200):
    """批量获取生命周期 (200个成员系统)"""
    return await lifecycle_service.batch_retrieve(count)


@router.get("/lifecycle/list")
async def get_lifecycle_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    member: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取生命周期数据列表"""
    return lifecycle_service.get_lifecycle_data(db, member, page, size)


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
