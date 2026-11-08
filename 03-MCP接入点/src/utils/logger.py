"""
日志管理工具
"""

import os
import sys
import logging
from typing import Optional
from loguru import logger
from .config import config

# 版本号
from . import __version__ as VERSION


class InterceptHandler(logging.Handler):
    """拦截标准库日志并转发到loguru"""

    def emit(self, record):
        # 获取对应的loguru级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 查找调用者
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == __file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class LoggerManager:
    """日志管理器"""

    def __init__(self):
        self._setup_logger()

    def _setup_logger(self):
        """设置日志器"""
        # 移除默认的处理器
        logger.remove()

        # 自定义格式：时间[版本号][模块路径]-级别-消息
        # 不同部分使用不同颜色
        custom_format = (
            f"<green>{{time:YYMMDD HH:mm:ss}}</green>"
            f"<blue>[{VERSION}][{{name}}]</blue>"
            "<level>-{level}-</level>"
            "<green>{message}</green>"
        )

        # 控制台处理器
        logger.add(
            sys.stdout,
            format=custom_format,
            level=config.get("server", "log_level", "INFO"),
            colorize=True,
            backtrace=True,
            diagnose=True,
            enqueue=True,
            catch=True,
        )

        # 文件处理器（不带颜色）
        log_file = config.get("logging", "log_file", "logs/mcp_server.log")
        if log_file:
            # 确保日志目录存在
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            # 文件格式（不带颜色）
            file_format = f"{{time:YYYY-MM-DD HH:mm:ss}} [{VERSION}][{{name}}] {{level}} - {{message}}"

            # 获取文件大小限制
            max_file_size = config.get("logging", "max_file_size", "10MB")
            max_bytes = self._parse_size(max_file_size)

            # 获取备份数量
            backup_count = config.getint("logging", "backup_count", 5)

            logger.add(
                log_file,
                format=file_format,
                level=config.get("server", "log_level", "INFO"),
                rotation=max_bytes,
                retention=backup_count,
                compression="zip",
                encoding="utf-8",
                enqueue=True,
                catch=True,
            )

    def _parse_size(self, size_str: str) -> int:
        """解析文件大小字符串"""
        size_str = size_str.upper()
        if size_str.endswith("MB"):
            return int(float(size_str[:-2]) * 1024 * 1024)
        elif size_str.endswith("KB"):
            return int(float(size_str[:-2]) * 1024)
        elif size_str.endswith("B"):
            return int(size_str[:-1])
        else:
            return int(size_str)

    def get_logger(self):
        """获取日志器"""
        return logger

    def reload(self):
        """重新加载日志配置"""
        self._setup_logger()

    def setup_uvicorn_logging(self):
        """设置uvicorn日志拦截"""
        import logging

        # 拦截标准库日志
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        # 直接禁用uvicorn.access日志
        logging.getLogger("uvicorn.access").disabled = True
        logging.getLogger("uvicorn.access").propagate = False

        # 拦截其他uvicorn日志
        for name in logging.root.manager.loggerDict.keys():
            if not name.startswith("uvicorn.access"):
                logging.getLogger(name).handlers = []
                logging.getLogger(name).propagate = True


# 全局日志管理器实例
logger_manager = LoggerManager()


def get_logger(name: str = "mcp_server"):
    """获取日志器"""
    return logger.bind(name=name)
