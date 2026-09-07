# -*- coding: utf-8 -*-
"""API 依赖与全局单例

FastAPI 启动时装配：配置 → 知识库 → LLM → 会话/学情 → 工作流引擎 → MCP 工具注册表。
"""
from __future__ import annotations

import logging

from fastapi import Request

logger = logging.getLogger("agent.api.deps")


def get_workflow(request: Request):
    return request.app.state.workflow


def get_kb(request: Request):
    return request.app.state.kb


def get_study(request: Request):
    return request.app.state.study


def get_sessions(request: Request):
    return request.app.state.sessions


def get_registry(request: Request):
    return request.app.state.registry
