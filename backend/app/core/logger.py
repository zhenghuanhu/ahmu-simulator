"""
日志系统 - loguru
循环日志, 总大小上限1GB, 防止磁盘写满
"""
import sys
from loguru import logger
from app.config import LOG_DIR, LOG_CONFIG


def setup_logging():
    """配置日志系统"""
    logger.remove()  # 移除默认处理器

    # 控制台输出
    logger.add(
        sys.stdout,
        format=LOG_CONFIG["format"],
        level=LOG_CONFIG["level"],
        colorize=True,
    )

    # 文件输出 - 循环覆盖, 总大小上限1GB
    logger.add(
        str(LOG_DIR / "ahmu_{time:YYYY-MM-DD}.log"),
        format=LOG_CONFIG["format"],
        level=LOG_CONFIG["level"],
        rotation=LOG_CONFIG["rotation"],
        retention=LOG_CONFIG["retention"],
        compression="zip",
        encoding="utf-8",
    )

    # 错误日志单独文件
    logger.add(
        str(LOG_DIR / "ahmu_error.log"),
        format=LOG_CONFIG["format"],
        level="ERROR",
        rotation="50 MB",
        retention=10,
        compression="zip",
        encoding="utf-8",
    )

    logger.info("日志系统已初始化")


def get_logger():
    return logger
