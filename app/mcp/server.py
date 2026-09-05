# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）服务端：JSON-RPC 2.0 over stdio

实现统一MCP协议的最小可运行子集：
- initialize：协议握手
- tools/list：工具列表（4个课程工具）
- tools/call：调用工具

运行方式：python -m app.mcp.server
与学校平台的对接说明见 README「MCP 对接」章节。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("agent.mcp.server")

PROTOCOL_VERSION = "2025-03-26"


class MCPServer:
    def __init__(self, registry):
        self.registry = registry

    def _handle(self, msg: dict) -> dict:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "robot-intro-course-agent", "version": "1.0.0"},
                }
            elif method == "notifications/initialized":
                return None  # 通知类不返回
            elif method == "tools/list":
                result = {"tools": self.registry.list()}
            elif method == "tools/call":
                name = params.get("name", "")
                args = params.get("arguments", {}) or {}
                payload = self.registry.call(name, args)
                result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                          "isError": False}
            elif method == "ping":
                result = {}
            else:
                return self._error(msg_id, -32601, f"Method not found: {method}")
        except KeyError as exc:
            return self._error(msg_id, -32602, f"Invalid params: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("工具调用失败")
            return self._error(msg_id, -32603, f"Internal error: {exc}")

        if result is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def serve_stdio(self) -> None:
        """从 stdin 逐行读取 JSON-RPC 请求，响应写入 stdout（每条一行）"""
        logger.info("MCP server (stdio) 启动，协议版本 %s", PROTOCOL_VERSION)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self._handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def run_server() -> None:
    """启动 MCP server（加载配置、知识库与工具注册表）

    路径一律解析为项目根目录的绝对路径：MCP 宿主（Claude Desktop / 学校平台等）
    以子进程方式启动本服务时，工作目录通常不是项目根，相对路径会导致索引/数据库找不到。
    """
    from ..core.config import PROJECT_ROOT, load_config
    from ..core.llm import create_llm
    from ..core.session import SessionManager
    from ..core.study_store import StudyStore
    from ..core.workflow import WorkflowEngine
    from ..rag.embedder import create_embedder
    from ..rag.knowledge_base import KnowledgeBase

    def _abs(p: str) -> Path:
        p = Path(p)
        return p if p.is_absolute() else PROJECT_ROOT / p

    cfg = load_config()
    kb_cfg = cfg.get("knowledge_base", {}) or {}
    index_dir = _abs(kb_cfg.get("index_dir", "data/index"))
    kb = KnowledgeBase(
        kb_cfg.get("source_dir", "data/knowledge_base"),
        index_dir,
        embedder=create_embedder(cfg),
        chunk_size=kb_cfg.get("chunk_size", 500),
        chunk_overlap=kb_cfg.get("chunk_overlap", 60),
        pdf_max_pages=kb_cfg.get("pdf_max_pages", 0),
    )
    if not kb.load():
        logger.error("知识库索引不存在（%s），请先运行 scripts/build_kb.py 构建", index_dir)
        sys.exit(1)

    llm = create_llm(cfg)
    study_db = cfg.get("study_db", {}).get("path", "data/study.db")
    study = StudyStore(_abs(study_db))
    sessions = SessionManager(cfg.get("server", {}).get("session_timeout_minutes", 30))
    ret_cfg = cfg.get("retrieval", {}) or {}
    workflow = WorkflowEngine(
        kb, llm, sessions, study,
        top_k=ret_cfg.get("top_k", 5), threshold=ret_cfg.get("threshold", 0.72),
        threshold_keyword=ret_cfg.get("threshold_keyword"),
        vector_weight=ret_cfg.get("vector_weight", 0.6),
        keyword_weight=ret_cfg.get("keyword_weight", 0.4),
    )
    from .tools import build_registry
    registry = build_registry(kb, workflow)
    logger.info("MCP 工具注册表就绪：%s", ", ".join(t["name"] for t in registry.list()))
    MCPServer(registry).serve_stdio()


if __name__ == "__main__":
    run_server()
