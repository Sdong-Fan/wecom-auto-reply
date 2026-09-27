# config/manager.py
"""Config 模块 — 配置管理

职责：
- 加载配置文件
- 支持点路径访问
- 支持热更新
"""

import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self._config: dict = {}
        self._last_modified: float = 0
        self._load()

    def _load(self):
        """加载配置文件"""
        try:
            with open(self.config_path, encoding="utf-8") as f:
                self._config = json.load(f)
            self._last_modified = os.path.getmtime(self.config_path)
            logger.info(f"配置已加载: {self.config_path}")
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            self._config = {}

    def get(self, dotpath: str, default: Any = None) -> Any:
        """点路径访问配置

        示例：
            config.get("red_dot.r_min")
            config.get("ai.model")
        """
        keys = dotpath.split(".")
        value = self._config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value is not None else default

    def reload(self) -> bool:
        """热更新配置文件"""
        try:
            mtime = os.path.getmtime(self.config_path)
            if mtime > self._last_modified:
                self._load()
                logger.info("配置已热更新")
                return True
        except Exception as e:
            logger.warning(f"检查配置更新失败: {e}")
        return False

    @property
    def config(self) -> dict:
        """获取完整配置"""
        return self._config

    def __getitem__(self, key: str) -> Any:
        return self._config[key]

    def __contains__(self, key: str) -> bool:
        return key in self._config
