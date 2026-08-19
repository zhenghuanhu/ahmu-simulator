# AHMU 仿真器 - Windows 本地开发指南

## 项目概述

AHMU仿真器基于 B/S 架构，后端使用 Python FastAPI，前端使用 Vue 3 + Element Plus。
下位机部署目标为 Linux，当前为 Windows 本地测试环境（使用 Mock 硬件接口）。

## 快速启动

### 方式一：一键启动
1. 双击 `start.bat` 或运行 `python start.py`
2. 自动检查并安装依赖、构建前端、启动服务
3. 浏览器自动打开 `http://127.0.0.1:8443`
4. 登录：用户名 `TEST`，密码 `123456`

### 方式二：手动启动（开发模式）

#### 1. 后端启动
```bash
cd backend
# 首次: 创建虚拟环境
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# 启动
venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8443 --reload
```

#### 2. 前端开发模式（热更新）
```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173 (自动代理API到后端8443)
```

#### 3. 前端构建（生产模式）
```bash
cd frontend
npm run build
# 构建产物在 frontend/dist/，后端自动服务
# 访问 http://127.0.0.1:8443
```

## 项目结构

```
ahmu-simulator/
├── start.py                 # 一键启动脚本
├── start.bat                # Windows批处理启动
├── backend/
│   ├── venv/                # Python虚拟环境
│   ├── app/
│   │   ├── main.py          # FastAPI入口 (启动所有服务)
│   │   ├── config.py        # 全局配置
│   │   ├── database.py      # SQLite + ORM模型
│   │   ├── api/
│   │   │   └── routes.py    # REST API + WebSocket路由
│   │   ├── core/
│   │   │   ├── websocket_manager.py  # WebSocket连接池
│   │   │   ├── arinc_mock.py         # Mock ARINC硬件接口
│   │   │   ├── icd_parser.py          # ICD解析引擎
│   │   │   ├── shared_memory.py       # 共享内存(Windows兼容)
│   │   │   └── logger.py             # 日志系统
│   │   └── services/
│   │       ├── fault_diagnosis.py    # 故障诊断服务
│   │       ├── param_monitor.py      # 参数监控服务
│   │       ├── config_management.py  # 构型管理服务
│   │       ├── startup_test.py       # 启动测试服务
│   │       ├── data_load.py          # 数据加载服务
│   │       ├── maintenance_mode.py   # 维护模式控制
│   │       ├── lifecycle.py          # 生命周期管理
│   │       ├── acars.py              # ACARS管理
│   │       └── print_mgr.py          # 打印管理
│   ├── data/               # SQLite数据库
│   ├── cache/              # ICD缓存
│   └── logs/               # 日志文件
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── main.js         # 入口
│   │   ├── router/         # 路由
│   │   ├── composables/
│   │   │   └── useWebSocket.js  # WebSocket通信
│   │   ├── assets/
│   │   │   └── ohms.css   # OHMS界面风格(黑色/白色/青色)
│   │   └── views/
│   │       ├── Login.vue           # 登录
│   │       ├── Layout.vue          # 主布局
│   │       ├── Dashboard.vue       # 系统总览
│   │       ├── FaultDiagnosis.vue  # 故障诊断
│   │       ├── ParamMonitor.vue    # 参数监控
│   │       ├── ConfigManagement.vue # 构型管理
│   │       ├── StartupTest.vue     # 启动测试
│   │       ├── DataLoad.vue        # 数据加载
│   │       ├── Lifecycle.vue       # 生命周期
│   │       ├── ACARS.vue           # ACARS管理
│   │       ├── Print.vue           # 打印管理
│   │       └── ICDManagement.vue   # ICD管理
│   └── dist/              # 构建产物
└── README.md
```

## 功能模块清单

| 模块 | API路径 | 功能说明 |
|------|---------|----------|
| 用户认证 | POST /api/v1/auth/login | TEST/123456登录 |
| 系统状态 | GET /api/v1/system/status | 实时系统状态总览 |
| 故障诊断 | GET/POST /api/v1/fault/* | 故障报告管理、历史查询、手动注入 |
| 参数监控 | GET /api/v1/params/* | 参数列表、历史、快捷列表 |
| 构型管理 | GET/POST /api/v1/config/* | 构型报告、批量验证(400个) |
| 启动测试 | POST /api/v1/groundtest/* | ARINC624状态机、ACK确认 |
| 数据加载 | POST /api/v1/dataload/* | ARINC615A加载、进度推送 |
| 生命周期 | POST /api/v1/lifecycle/* | 批量获取(200个成员系统) |
| ICD管理 | POST /api/v1/icd/* | 导入、解析、冲突检测 |
| ACARS | GET/POST /api/v1/acars/* | 上下行链路、优先级 |
| 打印管理 | GET/POST /api/v1/print/* | ARINC744A协议仿真 |
| WebSocket | ws://127.0.0.1:8443/ws/ahmu | 实时推送(故障/参数/状态) |

## 技术栈

### 后端
- **框架**: FastAPI 0.141 + Uvicorn (ASGI异步)
- **数据库**: SQLite + WAL模式 (Python sqlite3标准库)
- **ORM**: SQLAlchemy 2.0
- **数据校验**: Pydantic 2.6
- **日志**: Loguru (循环覆盖, 1GB上限)
- **定时任务**: APScheduler
- **二进制缓存**: msgpack

### 前端
- **框架**: Vue 3.4
- **UI库**: Element Plus 2.7
- **路由**: Vue Router 4.3
- **构建**: Vite 5.2
- **WebSocket**: 原生 + 自动重连composable

### Mock 硬件接口（Windows测试）
- ARINC664 Mock: 10条虚拟链路，模拟数据收发
- ARINC429 Mock: 20个Label通道
- ARINC825 Mock: 10个CAN ID通道
- 共享内存: Windows mmap兼容，环形缓冲区

### 生产环境适配（Linux下位机）
- 将 `arinc_mock.py` 替换为真实板卡SDK (ctypes调用.so)
- 共享内存切换为 `/dev/shm` 路径
- Systemd服务管理替代手动启动
- 添加硬件看门狗 + 软件看门狗
- 配置日志轮转 (logrotate)

## 开发步骤参考

1. **修改后端代码**: 编辑 `backend/app/` 下文件，`--reload` 自动热更新
2. **修改前端代码**: 编辑 `frontend/src/` 下文件，`npm run dev` 热更新
3. **重新构建前端**: `cd frontend && npm run build`，后端自动服务dist
4. **查看API文档**: 访问 `http://127.0.0.1:8443/docs` (Swagger UI)
5. **查看日志**: `backend/logs/ahmu_*.log`
6. **数据库**: `backend/data/ahmu.db` (可用DB Browser for SQLite查看)

## 注意事项

- Windows测试使用Mock硬件接口，不涉及真实ARINC板卡
- SQLite数据库在 `backend/data/` 下，删除后重启自动重建
- ICD演示文件在 `backend/cache/demo_icd.json`，可自定义修改
- 前端OHMS风格：黑色背景/白色文字/青色突出显示
- WebSocket最大5个终端同时接入
- 维护模式需满足3个条件持续30秒才能进入
