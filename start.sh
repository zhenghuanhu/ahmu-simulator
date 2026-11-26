#!/bin/bash
# =============================================================================
# AHMU 仿真器 - Linux 下位机启动脚本 (CentOS 7.9 实时操作系统 / 内核 3.10.0-1160)
#
# 功能:
#   1. 检测 Python 3.8+ (SCL rh-python38 或系统 python3.8/3.9/3.10/3.11)
#   2. 创建/校验虚拟环境 backend/venv
#   3. 从国内镜像安装依赖 (自动选用 requirements-py38.txt 兼容清单)
#   4. 启动 uvicorn 后端服务 (监听 0.0.0.0:8443)
#
# 用法:
#   ./start.sh           # 交互式启动 (前台运行, Ctrl+C 停止)
#   ./start.sh --daemon  # 后台启动 (日志写入 backend/logs)
#
# 生产部署建议使用 systemd 服务 (见 docs/centos7-adapter.md 或 ahmu-simulator.service)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/venv"
HOST="${AHMU_HOST:-0.0.0.0}"
PORT="${AHMU_PORT:-8443}"

# 国内 pip 镜像源 (容错顺序)
MIRRORS=(
    "https://mirrors.aliyun.com/pypi/simple/"
    "https://pypi.tuna.tsinghua.edu.cn/simple/"
    "https://pypi.mirrors.ustc.edu.cn/simple/"
)

info()  { echo -e "\033[1;32m[*]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[!]\033[0m $*"; }
error() { echo -e "\033[1;31m[✗]\033[0m $*"; }

# -----------------------------------------------------------------------------
# 1. 定位 Python 3.8+ 解释器 (CentOS 7.9 默认 python3=3.6 不满足, 需 SCL/编译)
# -----------------------------------------------------------------------------
find_python() {
    # 优先: SCL rh-python38 (CentOS 7 官方 Software Collection)
    local scl_candidates=(
        "/opt/rh/rh-python38/root/usr/bin/python3.8"
        "/opt/rh/rh-python39/root/usr/bin/python3.9"
        "/opt/rh/rh-python311/root/usr/bin/python3.11"
    )
    local py
    for py in "${scl_candidates[@]}"; do
        if [ -x "$py" ] && _py_ok "$py"; then
            echo "$py"; return 0
        fi
    done
    # 次选: PATH 中的 python3.8+
    for py in python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$py" >/dev/null 2>&1 && _py_ok "$py"; then
            echo "$(command -v "$py")"; return 0
        fi
    done
    return 1
}

_py_ok() {
    local py="$1"
    "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>/dev/null
}

# -----------------------------------------------------------------------------
# 2. 依赖清单选择 (3.8~3.10 用兼容降级版)
# -----------------------------------------------------------------------------
requirements_file() {
    local py="$1"
    local minor
    minor="$("$py" -c 'import sys; print(sys.version_info[1])' 2>/dev/null)"
    if [ "${minor:-0}" -lt 11 ] && [ -f "$BACKEND_DIR/requirements-py38.txt" ]; then
        echo "$BACKEND_DIR/requirements-py38.txt"
    else
        echo "$BACKEND_DIR/requirements.txt"
    fi
}

# -----------------------------------------------------------------------------
# 3. 虚拟环境就绪校验
# -----------------------------------------------------------------------------
venv_ready() {
    [ -x "$VENV_DIR/bin/python" ] || return 1
    "$VENV_DIR/bin/python" -c "import uvicorn, fastapi, sqlalchemy, pydantic" >/dev/null 2>&1
}

# -----------------------------------------------------------------------------
# 4. 安装依赖 (国内镜像容错)
# -----------------------------------------------------------------------------
install_requirements() {
    local req="$1"
    local m
    for m in "${MIRRORS[@]}"; do
        info "使用镜像源: $m"
        if "$VENV_DIR/bin/pip" install -r "$req" -i "$m" --timeout 60 --retries 3; then
            return 0
        fi
        warn "镜像 $m 安装失败, 尝试下一个..."
    done
    error "所有镜像均安装失败, 请检查网络或手动安装依赖"
    return 1
}

# -----------------------------------------------------------------------------
# 5. 主流程
# -----------------------------------------------------------------------------
main() {
    echo "============================================================"
    echo "  AHMU 仿真器 - Linux 下位机启动 (CentOS 7.9)"
    echo "============================================================"
    echo

    info "检查 Python 环境..."
    PYTHON="$(find_python)" || {
        error "未找到 Python 3.8+, 本项目最低要求 Python 3.8 (64位)。"
        error "CentOS 7.9 默认 python3 为 3.6, 请先安装 SCL rh-python38:"
        error "  yum install -y centos-release-scl"
        error "  yum install -y rh-python38"
        error "  (或用 /opt/rh/rh-python38/root/usr/bin/python3.8 编译/链接)"
        exit 1
    }
    info "使用 Python: $PYTHON ($("$PYTHON" --version 2>&1))"

    # 前端产物检查
    if [ ! -f "$SCRIPT_DIR/frontend/dist/index.html" ]; then
        error "未检测到前端构建产物 frontend/dist/index.html"
        error "请从开发机拷贝已构建的 frontend/dist 目录, 或安装 Node.js 后执行 npm run build"
        exit 1
    fi

    # 虚拟环境
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        info "创建虚拟环境 backend/venv ..."
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    if ! venv_ready; then
        warn "虚拟环境依赖不完整, 正在安装..."
        local req
        req="$(requirements_file "$PYTHON")"
        info "使用依赖清单: $(basename "$req")"
        install_requirements "$req"
    fi
    info "环境就绪"

    # 启动 uvicorn
    echo
    echo "  ========================================"
    echo "  |  访问地址:  http://$HOST:$PORT     |"
    echo "  |  API文档:  http://$HOST:$PORT/docs |"
    echo "  |  WebSocket: ws://$HOST:$PORT/ws/ahmu |"
    echo "  ========================================"
    echo "  默认登录: 用户名 TEST, 密码 123456"
    echo "  按 Ctrl+C 停止服务"
    echo

    cd "$BACKEND_DIR"
    if [ "${1:-}" = "--daemon" ]; then
        nohup "$VENV_DIR/bin/python" -m uvicorn app.main:app \
            --host "$HOST" --port "$PORT" \
            >> "$BACKEND_DIR/logs/uvicorn.log" 2>&1 &
        info "服务已后台启动 (PID $!), 日志: backend/logs/uvicorn.log"
    else
        exec "$VENV_DIR/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
    fi
}

main "$@"
