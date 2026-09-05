# -*- coding: utf-8 -*-
"""《机器人学导论》课程智能体 —— FastAPI 应用入口

运行方式：
    pip install -r requirements.txt
    python scripts/build_kb.py          # 首次：构建知识库索引
    uvicorn app.main:app --host 0.0.0.0 --port 8000

接口一览：
    GET  /health            健康检查
    POST /v1/chat           对话（含知识库检索/生成/追问）
    POST /v1/sessions       创建会话
    DELETE /v1/sessions/{id} 结束会话
    POST /v1/homework/judge  作业自动批阅
    POST /v1/feedback       答案反馈
    GET  /v1/insight        学情统计（教师端）
    GET  /v1/tools          MCP 工具清单
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import admin, chat, course, exercise, feedback, graph, homework, insight, sessions, student, videos
from .core.config import load_config
from .core.llm import create_llm
from .core.session import SessionManager
from .core.study_store import StudyStore
from .core.workflow import WorkflowEngine
from .mcp.tools import build_registry
from .rag.embedder import create_embedder
from .rag.knowledge_base import KnowledgeBase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("agent.main")

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_app() -> FastAPI:
    """装配应用（独立函数，便于测试直接调用）"""
    cfg = load_config()
    kb_cfg = cfg.get("knowledge_base", {}) or {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ---- 启动装配 ----
        index_dir = Path(kb_cfg.get("index_dir", "data/index"))
        if not index_dir.is_absolute():
            index_dir = PROJECT_ROOT / index_dir
        kb = KnowledgeBase(
            kb_cfg.get("source_dir", "data/knowledge_base"),
            index_dir,
            embedder=create_embedder(cfg),
            chunk_size=kb_cfg.get("chunk_size", 500),
            chunk_overlap=kb_cfg.get("chunk_overlap", 60),
            pdf_max_pages=kb_cfg.get("pdf_max_pages", 0),
        )
        if not kb.load():
            logger.error(
                "知识库索引不存在（%s）。请先运行: python scripts/build_kb.py",
                index_dir,
            )
            raise RuntimeError("知识库索引缺失，请先运行 scripts/build_kb.py")

        llm = create_llm(cfg)
        study = StudyStore(cfg.get("study_db", {}).get("path", "data/study.db"))
        sessions_mgr = SessionManager(cfg.get("server", {}).get("session_timeout_minutes", 30))
        ret_cfg = cfg.get("retrieval", {}) or {}
        workflow = WorkflowEngine(
            kb, llm, sessions_mgr, study,
            top_k=ret_cfg.get("top_k", 5), threshold=ret_cfg.get("threshold", 0.72),
            threshold_keyword=ret_cfg.get("threshold_keyword"),
            vector_weight=ret_cfg.get("vector_weight", 0.6),
            keyword_weight=ret_cfg.get("keyword_weight", 0.4),
        )
        registry = build_registry(kb, workflow)

        app.state.kb = kb
        app.state.llm = llm
        app.state.study = study
        app.state.sessions = sessions_mgr
        app.state.workflow = workflow
        app.state.registry = registry

        logger.info("课程智能体服务启动完成：知识库 %d 块，LLM=%s，Embedding=%s",
                    len(kb.chunks), type(llm).__name__, type(kb.embedder).__name__)
        yield
        study.close()

    app = FastAPI(
        title="《机器人学导论》课程智能体",
        description="成都理工大学2025年度“AI研究基金”项目（2025AI068）— AI赋能机器人学课程的教学改革和实践探索",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat.router)
    app.include_router(course.router)
    app.include_router(sessions.router)
    app.include_router(homework.router)
    app.include_router(feedback.router)
    app.include_router(insight.router)
    app.include_router(graph.router)
    app.include_router(exercise.router)
    app.include_router(student.router)
    app.include_router(admin.router)
    app.include_router(videos.router)

    @app.get("/health", summary="健康检查")
    def health() -> dict:
        return {"status": "ok", "service": "robot-intro-course-agent"}

    @app.get("/v1/tools", summary="MCP 工具清单")
    def tools() -> dict:
        registry = getattr(app.state, "registry", None)
        return {"tools": registry.list() if registry else []}

    # 视频文件静态服务（知识库 video 目录，供前端 <video> 播放）
    video_dir = kb_cfg.get("source_dir", "data/knowledge_base")
    if not Path(video_dir).is_absolute():
        video_dir = PROJECT_ROOT / video_dir
    video_dir = Path(video_dir) / "video"
    if video_dir.exists():
        app.mount("/videos", StaticFiles(directory=str(video_dir)), name="videos")

    # 课程资料静态服务（知识库根目录，供 PPT/教材/题库/大纲下载）
    kb_dir = kb_cfg.get("source_dir", "data/knowledge_base")
    if not Path(kb_dir).is_absolute():
        kb_dir = PROJECT_ROOT / kb_dir
    kb_dir = Path(kb_dir)
    if kb_dir.exists():
        app.mount("/resources", StaticFiles(directory=str(kb_dir)), name="resources")

    # 前端演示页面（app/static/index.html），置于最后注册以保证 API 路由优先匹配
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = build_app()

if __name__ == "__main__":
    import argparse
    import socket

    import uvicorn

    parser = argparse.ArgumentParser(description="《机器人学导论》课程智能体服务")
    parser.add_argument("--host", default=None, help="监听地址，默认读 config.yaml 或 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认读 config.yaml 或 8000")
    args = parser.parse_args()

    server_cfg = load_config().get("server", {}) or {}
    host = args.host or server_cfg.get("host", "0.0.0.0")
    port = args.port or server_cfg.get("port", 8000)

    # 端口占用预检：避免 uvicorn 抛出难以阅读的 Errno 10048
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            print(f"\n[启动失败] 端口 {port} 已被占用，通常是已有一个服务实例正在运行。")
            print(f"  · 直接使用已有服务：浏览器访问 http://localhost:{port}/")
            print(f"  · 启动新实例请先关闭占用进程，或换端口运行：python -m app.main --port 8001\n")
            sys.exit(1)

    print(f"服务启动中：前端页面 http://localhost:{port}/  ｜  API 文档 http://localhost:{port}/docs")
    uvicorn.run(app, host=host, port=port)
