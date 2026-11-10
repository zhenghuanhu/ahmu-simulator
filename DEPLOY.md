# AHMU 仿真器 — 部署与运行环境说明

本文档说明 AHMU 仿真器在**另一台 Windows 电脑**上运行所需的全部环境与依赖，以及打包、安装、运行的完整步骤。

---

## 一、项目技术栈与运行环境

| 类别 | 内容 | 版本 / 说明 |
| --- | --- | --- |
| 后端语言 | Python | **3.8 ~ 3.13（64 位）**，开发机为 3.13（3.8~3.10 自动使用兼容依赖清单 `requirements-py38.txt`） |
| 后端框架 | FastAPI + Uvicorn | FastAPI 0.141.1 / Uvicorn 0.52.3（ASGI 异步服务器） |
| 数据校验 | Pydantic | 2.13.4 |
| ORM / 数据库 | SQLAlchemy + SQLite | SQLAlchemy 2.0.52 / SQLite（WAL 模式，Python 内置 sqlite3，无独立数据库服务） |
| 日志 | Loguru | 0.7.3（循环覆盖，上限 1GB） |
| 定时任务 | APScheduler | 3.11.3 |
| 二进制缓存 | msgpack | 1.2.1 |
| 文件上传/下载 | python-multipart / aiofiles | 0.0.32 / 25.1.0 |
| 模板 | Jinja2 | 3.1.6 |
| 前端 | Vue 3 + Element Plus + Vite | **已构建为静态文件（dist），运行时无需 Node.js/npm** |

> 完整依赖清单见 `backend/requirements.txt`（共 10 个包，全部为纯 Python 或带预编译 wheel 的通用库，无 C 编译要求）。

## 二、目标电脑环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（64 位） |
| Python | **3.8 ~ 3.13（64 位）**，**安装时勾选「Add Python to PATH」**（3.8~3.10 也能运行，脚本会自动选用兼容依赖） |
| Node.js / npm | **不需要**（前端已构建为静态文件） |
| 网络 | 首次运行需联网（自动从国内镜像安装依赖，约 50 MB） |
| 磁盘空间 | 约 500 MB（含虚拟环境与依赖） |
| 内存 | 2 GB 以上 |

## 三、端口与网络

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| 8443 | TCP/HTTP | Web 服务（浏览器访问 + REST API + WebSocket） |
| 34800 ~ 34897 | UDP（本机回环） | 模拟 AFDX/ARINC 通信（故障/失效/构型/配平/ACARS/打印/飞机状态等各通道） |
| 59 | UDP（本机回环） | TFTP 数据加载 |

> 全部端口仅在 `127.0.0.1` 回环使用，不对外监听。首次运行若被 Windows 防火墙拦截，请点「允许访问」。

## 四、配置文件与数据文件

| 文件 / 目录 | 是否需打包 | 说明 |
| --- | --- | --- |
| `backend/app/config.py` | 是（源码内） | 全局配置（端口、数据库路径、各功能参数） |
| `backend/config/nvm_reset_config.json` | 是 | 数据重置服务的成员系统列表（52 个成员） |
| `backend/app/`（42 个 .py） | 是 | 后端全部源码 |
| `backend/requirements.txt` | 是 | 后端依赖清单 |
| `frontend/dist/` | 是 | 前端构建产物（index.html + assets） |
| `backend/data/ahmu.db` | 否 | SQLite 数据库，**首次启动自动创建**（create_all + schema 迁移） |
| `backend/cache/*` | 否 | 运行时缓存（ICD 快照 msgpack、上传的 XML），首次启动自动生成 demo_icd.json |
| `backend/logs/` | 否 | 日志，运行时生成 |
| `backend/venv/` | 否 | 虚拟环境，**目标电脑上自动重建**（venv 不能跨机器拷贝） |

> 关键结论：**唯一不能跨机复用、也无需打包的是 `venv`**（虚拟环境路径绑定 + Python 版本绑定），其余运行时产物（数据库、缓存、日志）均由程序首次启动自动生成。

## 五、打包方式

使用项目根目录的 `pack_release.py` 一键打包：

```bash
python pack_release.py 1.0.0
```

脚本会自动：
1. 校验 `frontend/dist/index.html` 是否存在（不存在则提示先 `npm run build`）；
2. 收集必需文件（后端源码、requirements、config、前端 dist、启动脚本）；
3. **自动排除** venv、node_modules、`__pycache__`、tests、docs、data、logs、cache 等运行时/开发产物；
4. 生成 `release/ahmu-simulator-<版本>.zip` 发布包，并内置「使用说明.txt」。

## 六、安装运行步骤（目标电脑）

1. **安装 Python**：到 python.org 下载 Python 3.11+（64 位）安装包，**务必勾选「Add Python to PATH」**，完成后重启命令行验证 `python --version`。
2. **解压发布包**：将 `ahmu-simulator-<版本>.zip` 解压到任意目录（路径建议不含中文和空格，如 `D:\ahmu-simulator`）。
3. **双击 `start.bat`**（或运行 `python start.py`）。
4. 首次启动会自动执行：
   - 创建 Python 虚拟环境 `backend\venv`；
   - 从**国内镜像**（阿里云 → 清华 → 中科大，自动容错）安装依赖；
   - 启动后端服务。
5. 浏览器自动打开 `http://127.0.0.1:8443`，登录：用户名 `TEST`、密码 `123456`。
6. 停止服务：关闭启动窗口或按 `Ctrl+C`。

## 七、常见问题

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `Python not found` | Python 未装或未加 PATH | 重装 Python 3.8+ 并勾选 Add to PATH |
| `Python 版本过低` | 系统 Python 低于 3.8 | 安装 Python 3.8+（推荐 3.11+）并确保 PATH 指向新版本 |
| `No module named uvicorn` | venv 存在但依赖未装全（安装中途失败） | 删除 `backend/venv` 后重试；脚本已自动补装缺失依赖 |
| 依赖安装超时/失败 | 网络访问国外 PyPI 超时 | 脚本已内置国内镜像；若仍失败检查网络或手动执行镜像安装 |
| `npm 不是内部或外部命令` | 缺前端 dist 且无 Node.js | 用含 `frontend/dist` 的发布包，或安装 Node.js 重新构建 |
| 端口 8443 被占用 | 其他程序占用 | 关闭占用程序后重试（脚本会自动清理 8443） |
| 需要重置数据 | 数据库含历史数据 | 删除 `backend/data` 目录后重启，自动重建 |
