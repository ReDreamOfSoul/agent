# -*- coding: utf-8 -*-
"""会话管理接口：POST /v1/sessions、DELETE /v1/sessions/{id}"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.session import SessionManager
from ..core.study_store import StudyStore
from .deps import get_sessions, get_study

router = APIRouter(prefix="/v1", tags=["会话"])


class SessionCreate(BaseModel):
    pass


class SessionOut(BaseModel):
    session_id: str
    timeout_minutes: float


@router.post("/sessions", response_model=SessionOut, summary="创建会话")
def create_session(sessions: SessionManager = Depends(get_sessions),
                   study: StudyStore = Depends(get_study)) -> SessionOut:
    session_id = sessions.create()
    study.touch_session(session_id)
    return SessionOut(session_id=session_id, timeout_minutes=sessions.timeout_seconds / 60)


@router.delete("/sessions/{session_id}", summary="结束会话")
def end_session(session_id: str, sessions: SessionManager = Depends(get_sessions)) -> dict:
    sessions.end(session_id)
    return {"status": "ok", "session_id": session_id}
