"""
配置管理工具
"""

import os
import configparser
import uuid
from typing import Optional
from pathlib import Path


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_file: str = "data/.mcp-endpoint-server.cfg"):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        self._load_config()

    def _load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_file):
            self.config.read(self.config_file, encoding="utf-8")
            # 检查并生成key
            self._check_and_generate_key()
        else:
            # 如果配置文件不存在，从根目录拷贝
            self._copy_config_from_root()

    def _copy_config_from_root(self):
        """从根目录拷贝配置文件到data目录"""
        root_config = "mcp-endpoint-server.cfg"
        if os.path.exists(root_config):
            # 确保data目录存在
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

            # 拷贝配置文件
            import shutil

            shutil.copy2(root_config, self.config_file)

            # 重新加载配置
            self.config.read(self.config_file, encoding="utf-8")
            # 检查并生成key
            self._check_and_generate_key()
        else:
            # 如果根目录也没有配置文件，则创建默认配置
            self._create_default_config()

    def _check_and_generate_key(self):
        """检查key是否存在且长度足够，如果不足则生成新的"""
        try:
            current_key = self.config.get("server", "key", fallback="")
            if not current_key or len(current_key) < 32:
                # 生成32位随机密码
                new_key = self._generate_random_key()
                self.config.set("server", "key", new_key)

                # 保存到配置文件
                with open(self.config_file, "w", encoding="utf-8") as f:
                    self.config.write(f)

                print(f"已自动生成新的32位密钥: {new_key}")
        except Exception as e:
            print(f"检查密钥时发生错误: {e}")

    def _generate_random_key(self) -> str:
        """生成指定长度的随机密钥"""
        # 使用UUID生成密钥，移除连字符
        return str(uuid.uuid4()).replace("-", "")

    def _create_default_config(self):
        """创建默认配置"""
        self.config["server"] = {
            "host": "127.0.0.1",
            "port": "8004",
            "debug": "false",
            "log_level": "INFO",
            "key": self._generate_random_key(),  # 生成默认密钥
        }

        self.config["websocket"] = {
            "max_connections": "1000",
            "ping_interval": "30",
            "ping_timeout": "10",
            "close_timeout": "10",
        }

        self.config["security"] = {"allowed_origins": "*", "enable_cors": "true"}

        self.config["logging"] = {
            "log_file": "logs/mcp_server.log",
            "max_file_size": "10MB",
            "backup_count": "5",
        }

        # 确保目录存在
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

        # 保存默认配置
        with open(self.config_file, "w", encoding="utf-8") as f:
            self.config.write(f)

    def get(self, section: str, key: str, default: Optional[str] = None) -> str:
        """获取配置值"""
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def getint(self, section: str, key: str, default: int = 0) -> int:
        """获取整数配置值"""
        try:
            return self.config.getint(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def getboolean(self, section: str, key: str, default: bool = False) -> bool:
        """获取布尔配置值"""
        try:
            return self.config.getboolean(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def reload(self):
        """重新加载配置"""
        self._load_config()


# 全局配置实例
config = ConfigManager()
