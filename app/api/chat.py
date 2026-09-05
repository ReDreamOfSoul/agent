# -*- coding: utf-8 -*-
"""对话接口：POST /v1/chat、POST /v1/chat/stream

对应设计文档 7.2 应用接口设计。支持会话级多轮上下文（session_id 为空时自动创建会话）。
stream 端点以 SSE 分句推送，供前端「打字机」效果展示（内容与 /v1/chat 完全一致）。
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..core.workflow import WorkflowEngine
from .deps import get_sessions, get_workflow
from ..core.session import SessionManager

logger = logging.getLogger("agent.api.chat")

router = APIRouter(prefix="/v1", tags=["对话"])


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, description="会话ID，为空则自动创建")
    question: str = Field(..., min_length=1, max_length=2000, description="学生问题")


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    knowledge_base_hit: bool
    best_score: float
    retrieval_mode: str
    generation_mode: str
    answer: str
    sources: list[dict]
    followup_question: str
    trace: list[dict]


def _resolve_session(req: ChatRequest, workflow: WorkflowEngine,
                     sessions: SessionManager) -> str:
    session_id = req.session_id
    if not session_id:
        session_id = sessions.create()
        workflow.study.touch_session(session_id)
    elif not sessions.is_valid(session_id):
        # 会话不存在或超时：重建
        session_id = sessions.create()
        workflow.study.touch_session(session_id)
    return session_id


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _split_sentences(text: str, max_len: int = 24) -> list[str]:
    """把回答切成适合打字机节奏的分句（按标点/换行切，过长的句再拆）"""
    parts = re.split(r"(?<=[。！？；\n])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > max_len:
            cut = p[:max_len]
            out.append(cut)
            p = p[max_len:]
        if p:
            out.append(p)
    return out


@router.post("/chat", response_model=ChatResponse, summary="课程智能体对话")
def chat(req: ChatRequest, workflow: WorkflowEngine = Depends(get_workflow),
         sessions: SessionManager = Depends(get_sessions)) -> ChatResponse:
    session_id = _resolve_session(req, workflow, sessions)

    try:
        result = workflow.run(session_id, req.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("对话处理失败")
        raise HTTPException(status_code=500, detail=f"智能体服务异常: {exc}") from exc

    return ChatResponse(
        session_id=result.session_id,
        intent=result.intent,
        knowledge_base_hit=result.hit,
        best_score=result.best_score,
        retrieval_mode=result.retrieval_mode,
        generation_mode=result.generation_mode,
        answer=result.answer,
        sources=result.sources,
        followup_question=result.followup_question,
        trace=result.trace,
    )


@router.post("/chat/stream", summary="课程智能体对话（SSE 流式）")
def chat_stream(req: ChatRequest, workflow: WorkflowEngine = Depends(get_workflow),
                sessions: SessionManager = Depends(get_sessions)) -> StreamingResponse:
    """SSE 事件序列：meta（命中/来源/意图）→ delta（分句文本）→ done"""
    session_id = _resolve_session(req, workflow, sessions)

    def gen():
        try:
            result = workflow.run(session_id, req.question)
        except ValueError as exc:
            yield _sse("error", {"detail": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("对话处理失败")
            yield _sse("error", {"detail": f"智能体服务异常: {exc}"})
            return
        yield _sse("meta", {
            "session_id": result.session_id, "intent": result.intent,
            "knowledge_base_hit": result.hit, "best_score": result.best_score,
            "retrieval_mode": result.retrieval_mode,
            "generation_mode": result.generation_mode,
            "sources": result.sources, "followup_question": result.followup_question,
            "trace": result.trace,
        })
        for piece in _split_sentences(result.answer):
            yield _sse("delta", {"text": piece})
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
