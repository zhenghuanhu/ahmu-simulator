"""
AHMU 仿真器 - FastAPI 主应用
B/S架构后端入口, 负责启动所有服务模块
"""
import os
import sys
import asyncio
import json
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from loguru import logger

from app.config import HOST, PORT, DEBUG, RELOAD, WS_PATH, BASE_DIR
from app.core.logger import setup_logging
from app.core.websocket_manager import ws_manager
from app.core.arinc_mock import hardware
from app.core.icd_parser import icd_parser
from app.core.shared_memory import shm_channel
from app.database import init_database
from app.api.routes import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动与关闭"""
    # ==================== 启动 ====================
    setup_logging()
    logger.info("=" * 60)
    logger.info("AHMU 仿真器启动中...")
    logger.info("=" * 60)

    # 1. 初始化数据库
    init_database()
    logger.info("数据库已初始化 (SQLite + WAL)")

    # 2. 初始化硬件接口 (Mock)
    await hardware.initialize()

    # 3. 加载ICD数据 (优先从快照加载)
    if not icd_parser.load_cache():
        # 无快照, 生成演示ICD并解析
        from app.config import CACHE_DIR
        demo_path = str(CACHE_DIR / "demo_icd.json")
        if not os.path.exists(demo_path):
            icd_parser.generate_demo_icd(demo_path, member_count=20)
        icd_parser.parse_icd_file(demo_path)
    else:
        logger.info("ICD快照加载成功, 跳过重复解析")

    # 4. 初始化共享内存
    shm_channel.initialize()

    # 5. 启动业务服务
    from app.services.fault_diagnosis import fault_service
    from app.services.param_monitor import param_service
    from app.services.config_management import config_service
    from app.services.startup_test import startup_test_service
    from app.services.data_load import data_load_service
    from app.services.maintenance_mode import maintenance_service
    from app.services.lifecycle import lifecycle_service
    from app.services.acars import acars_service
    from app.services.print_mgr import print_service

    await fault_service.start()
    await param_service.start()
    await config_service.start()
    await startup_test_service.start()
    await data_load_service.start()
    await maintenance_service.start()
    await lifecycle_service.start()
    await acars_service.start()
    await print_service.start()

    logger.info("=" * 60)
    logger.info("AHMU 仿真器启动完成!")
    logger.info(f"  服务地址: http://{HOST}:{PORT}")
    logger.info(f"  API文档: http://{HOST}:{PORT}/docs")
    logger.info(f"  WebSocket: ws://{HOST}:{PORT}{WS_PATH}")
    logger.info("=" * 60)

    yield

    # ==================== 关闭 ====================
    logger.info("AHMU 仿真器关闭中...")

    await fault_service.stop()
    await param_service.stop()
    await config_service.stop()
    await startup_test_service.stop()
    await data_load_service.stop()
    await maintenance_service.stop()
    await lifecycle_service.stop()
    await acars_service.stop()
    await print_service.stop()

    await hardware.shutdown()
    shm_channel.close()

    logger.info("AHMU 仿真器已关闭")


# 创建FastAPI应用
app = FastAPI(
    title="AHMU 仿真器",
    description="AHMU仿真器 B/S架构 - Python后端 (Windows本地测试版)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS配置 (允许前端开发服务器跨域)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册API路由
app.include_router(api_router)


# ==================== WebSocket端点 ====================

@app.websocket(WS_PATH)
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket实时推送通道"""
    connected = await ws_manager.connect(websocket)
    if not connected:
        return

    try:
        while True:
            # 接收前端消息
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "ping":
                await ws_manager.send_to(websocket, "pong", {"timestamp": datetime.utcnow().isoformat()})

            elif msg_type == "subscribe":
                # 订阅特定事件
                event = data.get("event", "")
                logger.info(f"客户端订阅事件: {event}")

            elif msg_type == "command":
                # 处理前端命令
                command = data.get("command", "")
                logger.info(f"收到命令: {command}")

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket异常: {e}")
        ws_manager.disconnect(websocket)


# ==================== 静态文件与前端 ====================

# 前端构建产物目录
frontend_dist = BASE_DIR.parent / "frontend" / "dist"

# 始终挂载assets (如果存在)
if (frontend_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

@app.get("/")
async def serve_root():
    """根路径返回前端index.html"""
    index_path = frontend_dist / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>AHMU Simulator Backend</h1><p>Frontend not built. Run: cd frontend && npm run build</p>")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    """SPA前端路由 - 所有非API路径返回index.html"""
    if full_path.startswith(("api", "docs", "ws", "openapi", "redoc", "assets")):
        return HTMLResponse("Not Found", status_code=404)
    index_path = frontend_dist / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("Not Found", status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=RELOAD,
        log_level="info",
    )
