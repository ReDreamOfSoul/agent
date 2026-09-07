# -*- coding: utf-8 -*-
"""MCP stdio 服务入口（供外部 MCP 宿主以绝对路径启动）

用法（工作目录任意）：
    python C:/path/to/项目根/mcp_server.py

MCP 宿主（Claude Desktop / Cursor / 学校教学平台等）以子进程方式启动本脚本，
通过 stdin/stdout 进行 JSON-RPC 2.0 通信；脚本自行定位项目根，不依赖启动时的工作目录。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mcp.server import run_server  # noqa: E402

if __name__ == "__main__":
    run_server()
