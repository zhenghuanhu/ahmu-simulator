#!/usr/bin/env python3
"""
AHMU 仿真器 - 发布包打包脚本
生成可在其他 Windows 电脑上运行的绿色发布包 (zip)

用法: python pack_release.py [版本号]
示例: python pack_release.py 1.0.0
"""
import sys
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_VERSION = "1.0.0"

# 需要打包的路径 (相对于项目根)
INCLUDE_PATHS = [
    "start.py",
    "start.bat",
    "README.md",
    "DEPLOY.md",
    "backend/app",              # 后端源码
    "backend/requirements.txt",  # 后端依赖 (Python 3.11+)
    "backend/requirements-py38.txt",  # 后端依赖 (Python 3.8~3.10 兼容版)
    "backend/config",           # 配置文件 (nvm_reset_config.json)
    "frontend/dist",            # 前端构建产物
]

# 拷贝时排除的目录名 (运行时产物/开发产物/体积大文件)
EXCLUDE_DIRS = {
    "__pycache__", ".git", "venv", ".venv", "node_modules",
    "tests", "docs", "data", "logs", "cache", ".workbuddy",
}

# 拷贝时排除的文件后缀
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def copy_item(src: Path, dst: Path):
    """递归拷贝文件/目录, 跳过排除项"""
    if src.is_file():
        if src.suffix in EXCLUDE_SUFFIXES:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    if src.is_dir():
        if src.name in EXCLUDE_DIRS:
            return
        for child in src.iterdir():
            copy_item(child, dst / child.name)


def write_usage(dst: Path, version: str):
    """生成包内使用说明"""
    text = (
        "========================================\n"
        f"AHMU 仿真器 v{version} 使用说明\n"
        "========================================\n\n"
        "【环境要求】\n"
        "  1. Windows 10/11 (64 位)\n"
        "  2. Python 3.8 ~ 3.13 (64 位), 安装时勾选 Add to PATH\n"
        "  3. 无需 Node.js/npm (前端已构建)\n\n"
        "【运行步骤】\n"
        "  1. 解压本包到任意目录 (建议路径不含中文和空格)\n"
        "  2. 双击 start.bat (或运行 python start.py)\n"
        "  3. 首次运行自动创建虚拟环境并从国内镜像安装依赖\n"
        "  4. 浏览器自动打开 http://127.0.0.1:8443\n"
        "  5. 登录: 用户名 TEST, 密码 123456\n\n"
        "【常见问题】\n"
        "  - Python not found: 重装 Python 并勾选 Add to PATH\n"
        "  - 依赖安装失败: 脚本已内置国内镜像, 仍失败请检查网络\n"
        "  - 端口占用: 关闭占用 8443 端口的程序后重试\n"
        "  - 重置数据: 删除 backend/data 目录后重启\n\n"
        "详细说明见 DEPLOY.md\n"
    )
    (dst / "使用说明.txt").write_text(text, encoding="utf-8")


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VERSION

    # 校验前端已构建
    dist_index = SCRIPT_DIR / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        print("[!] 未找到 frontend/dist/index.html")
        print("    请先构建前端: cd frontend && npm install && npm run build")
        sys.exit(1)

    release_dir = SCRIPT_DIR / "release"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pkg_name = f"ahmu-simulator-{version}-{stamp}"
    build_dir = release_dir / pkg_name
    build_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] 打包版本: {version}")
    for rel in INCLUDE_PATHS:
        src = SCRIPT_DIR / rel
        if not src.exists():
            print(f"[!] 跳过不存在的路径: {rel}")
            continue
        copy_item(src, build_dir / rel)
        print(f"  [+] {rel}")

    write_usage(build_dir, version)

    # 打 zip (带时间戳命名, 不覆盖删除旧包)
    zip_path = release_dir / f"{pkg_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(build_dir.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(release_dir))

    size_mb = zip_path.stat().st_size / 1024 / 1024
    py_count = sum(1 for _ in build_dir.rglob("*.py"))
    print("\n[OK] 发布包已生成:")
    print(f"  {zip_path}")
    print(f"  大小: {size_mb:.2f} MB")
    print(f"  后端源码文件数: {py_count}")


if __name__ == "__main__":
    main()
