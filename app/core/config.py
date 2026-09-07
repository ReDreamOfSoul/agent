# -*- coding: utf-8 -*-
"""配置加载：YAML 配置 + 环境变量覆盖

优先级：环境变量（.env / 系统环境） > config/config.yaml 默认值
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

# 项目根目录（app/core/config.py 的上上级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def _load_env_file(env_path: Path) -> None:
    """读取 .env 文件中的 KEY=VALUE 到环境变量（不覆盖已存在的环境变量）"""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并字典，override 优先"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class Config:
    """全局配置对象，属性访问式读取"""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(f"配置项不存在: {name}") from exc

    def get(self, key: str, default: Any = None) -> Any:
        """支持点号路径读取，例如 cfg.get('retrieval.top_k')"""
        node: Any = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def to_dict(self) -> Dict[str, Any]:
        return self._data


def load_config(config_path: str | Path | None = None) -> Config:
    """加载配置：YAML + 环境变量覆盖"""
    config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # 读取项目根目录 .env（若存在）
    _load_env_file(PROJECT_ROOT / ".env")

    # 环境变量覆盖
    env_map = {
        "LLM_API_BASE_URL": ("llm", "api_base_url"),
        "LLM_API_KEY": ("llm", "api_key"),
        "LLM_MODEL": ("llm", "api_model"),
        "EMBEDDING_API_BASE_URL": ("embedding", "api_base_url"),
        "EMBEDDING_API_KEY": ("embedding", "api_key"),
        "EMBEDDING_MODEL": ("embedding", "api_model"),
        "STUDY_DB_PATH": ("study_db", "path"),
    }
    for env_name, (sec, key) in env_map.items():
        value = os.environ.get(env_name)
        if value:
            data.setdefault(sec, {})[key] = value

    # 若配置了向量服务环境变量，自动切换 provider
    if os.environ.get("EMBEDDING_API_BASE_URL") and os.environ.get("EMBEDDING_API_KEY"):
        data.setdefault("embedding", {})["provider"] = "api"
    if os.environ.get("LLM_API_BASE_URL") and os.environ.get("LLM_API_KEY"):
        data.setdefault("llm", {})["provider"] = "openai"

    return Config(data)
