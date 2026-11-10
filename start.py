"""
AHMU 仿真器 - 一键启动脚本 (Windows)
启动后端服务 + 提供前端访问地址
"""
import subprocess
import sys
import os
import shutil
import time
import socket
import threading
from pathlib import Path

# 项目路径
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR / "backend"
VENV_PYTHON = BACKEND_DIR / "venv" / "Scripts" / "python.exe"

def check_python_version():
    """检查 Python 版本 (最低 3.8; 3.8~3.10 自动使用兼容依赖清单)"""
    if sys.version_info < (3, 8):
        print(f"[!] 当前 Python 版本过低: {sys.version.split()[0]}")
        print("    本项目最低要求 Python 3.8 (64位)。")
        print("    请安装 Python 3.8+ 并勾选 'Add Python to PATH' 后重试。")
        sys.exit(1)


def requirements_file():
    """按 Python 版本选择依赖清单:
    - 3.8 ~ 3.10: 使用兼容降级版 requirements-py38.txt (完整版依赖要求 3.10/3.11+)
    - 3.11+     : 使用完整版 requirements.txt
    """
    if sys.version_info < (3, 11):
        compat = BACKEND_DIR / "requirements-py38.txt"
        if compat.exists():
            return compat
    return BACKEND_DIR / "requirements.txt"


def _venv_ready():
    """校验虚拟环境关键依赖是否安装完整 (uvicorn/fastapi/sqlalchemy 为启动必需)"""
    try:
        r = subprocess.run(
            [str(VENV_PYTHON), "-c", "import uvicorn, fastapi, sqlalchemy, pydantic"],
            capture_output=True, timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


def check_venv():
    """检查虚拟环境"""
    if not VENV_PYTHON.exists():
        print("[!] Python虚拟环境不存在, 正在创建...")
        py = sys.executable
        subprocess.run([py, "-m", "venv", str(BACKEND_DIR / "venv")], check=True)
        print("[*] 安装依赖...")
        pip = str(BACKEND_DIR / "venv" / "Scripts" / "pip.exe")
        _install_requirements(pip)
        return
    # venv 已存在, 但需校验依赖是否完整 (防止上次安装中途失败/版本不兼容)
    if not _venv_ready():
        print("[!] 虚拟环境存在但依赖不完整, 正在补装...")
        pip = str(BACKEND_DIR / "venv" / "Scripts" / "pip.exe")
        _install_requirements(pip)


def _install_requirements(pip):
    """安装后端依赖: 优先国内镜像, 多镜像容错, 避免访问 pypi.org 超时"""
    req = str(requirements_file())
    mirrors = [
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "https://pypi.mirrors.ustc.edu.cn/simple/",
    ]
    last_err = None
    for mirror in mirrors:
        print(f"  [*] 使用镜像源: {mirror}")
        try:
            subprocess.run(
                [pip, "install", "-r", req,
                 "-i", mirror,
                 "--timeout", "60",
                 "--retries", "3"],
                check=True,
            )
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            print(f"  [!] 镜像 {mirror} 安装失败, 尝试下一个...")
    if last_err:
        raise last_err

def check_frontend():
    """检查前端是否已构建"""
    dist_dir = SCRIPT_DIR / "frontend" / "dist" / "index.html"
    if dist_dir.exists():
        return
    npm = shutil.which("npm")
    if not npm:
        print("[!] 未检测到前端构建产物 (frontend/dist), 且本机未安装 Node.js/npm。")
        print("    请任选其一解决后重新启动:")
        print("    1) 从开发机拷贝已构建好的 frontend/dist 目录到本项目 frontend/ 下;")
        print("    2) 安装 Node.js (含 npm) 并加入 PATH, 再重新运行本脚本。")
        raise RuntimeError("前端未构建且缺少 npm, 无法自动构建前端")
    print("[!] 前端未构建, 正在构建...")
    os.chdir(str(SCRIPT_DIR / "frontend"))
    subprocess.run([npm, "install"], check=True, shell=True)
    subprocess.run([npm, "run", "build"], check=True, shell=True)
    os.chdir(str(SCRIPT_DIR))

def kill_port(port):
    """终止占用指定端口的进程"""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
             f"Select-Object -ExpandProperty OwningProcess"],
            capture_output=True, text=True, timeout=10
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        for pid in pids:
            if pid and pid != str(os.getpid()):
                print(f"  [*] 终止占用端口 {port} 的进程 (PID: {pid})")
                subprocess.run(["powershell", "-Command",
                                f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                               timeout=10)
                time.sleep(1)
    except Exception:
        pass

def wait_for_server(host, port, timeout=30):
    """等待服务器就绪"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.create_connection((host, port), timeout=2)
            sock.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.5)
    return False

def open_browser_windows(url):
    """使用 Windows 原生方式打开浏览器"""
    # 方法1: os.startfile (最可靠)
    try:
        os.startfile(url)
        return
    except Exception:
        pass
    # 方法2: subprocess cmd /c start
    try:
        subprocess.run(["cmd", "/c", "start", "", url], shell=False)
        return
    except Exception:
        pass
    # 方法3: webbrowser 兜底
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass

def start_server():
    """启动后端服务"""
    print()
    print("=" * 60)
    print("  AHMU 仿真器 - Windows本地测试启动")
    print("=" * 60)
    print()
    print("  [*] 检查环境...")
    check_python_version()
    check_venv()
    check_frontend()
    print("  [OK] 环境就绪")

    # 清理端口占用
    print("  [*] 检查端口占用...")
    kill_port(8443)
    print("  [OK] 端口 8443 可用")
    print()
    print("  [*] 启动后端服务...")
    print(f"      Python: {VENV_PYTHON}")
    print(f"      工作目录: {BACKEND_DIR}")
    print()
    print("  ========================================")
    print("  |  访问地址:  http://127.0.0.1:8443     |")
    print("  |  API文档:  http://127.0.0.1:8443/docs |")
    print("  |  WebSocket: ws://127.0.0.1:8443/ws/ahmu |")
    print("  ========================================")
    print()
    print("  默认登录: 用户名 TEST, 密码 123456")
    print("  按 Ctrl+C 停止服务")
    print()

    # 在后台线程中等待服务器就绪后打开浏览器
    def delayed_open_browser():
        if wait_for_server("127.0.0.1", 8443, timeout=30):
            print("\n  [OK] 服务器已就绪, 正在打开浏览器...")
            open_browser_windows("http://127.0.0.1:8443")
        else:
            print("\n  [!] 服务器启动超时, 请手动访问 http://127.0.0.1:8443")

    threading.Thread(target=delayed_open_browser, daemon=True).start()

    # 启动uvicorn
    cmd = [
        str(VENV_PYTHON), "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8443",
    ]
    os.chdir(str(BACKEND_DIR))
    subprocess.run(cmd)

if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        print("\n[*] 服务已停止")
    except Exception as e:
        print(f"\n[!] 启动失败: {e}")
        input("按回车键退出...")
