# -*- coding: utf-8 -*-
"""会话管理接口：POST /v1/sessions、DELETE /v1/sessions/{id}"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

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

@router.get("/sessions/history", summary="历史会话列表")
def history_sessions(limit: int = 50) -> dict:
    """从旧study.db读取历史会话列表"""
    db_path = Path(__file__).resolve().parents[2] / "data" / "study.db"
    if not db_path.exists():
        return {"sessions": []}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT session_id, created_at, last_active, turn_count FROM sessions ORDER BY last_active DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    sessions = []
    for r in rows:
        # 取第一条消息作为标题
        cur2 = conn.cursor()
        cur2.execute("SELECT question FROM interactions WHERE session_id=? ORDER BY ts ASC LIMIT 1", (r["session_id"],))
        first = cur2.fetchone()
        title = first["question"][:30] + "..." if first and first["question"] else "新会话"
        sessions.append({
            "session_id": r["session_id"],
            "title": title,
            "created_at": r["created_at"],
            "last_active": r["last_active"],
            "turn_count": r["turn_count"],
            "time_str": time.strftime("%m-%d %H:%M", time.localtime(r["last_active"])) if r["last_active"] else "",
        })
    conn.close()
    return {"sessions": sessions}


@router.get("/sessions/{session_id}/messages", summary="获取会话历史消息")
def session_messages(session_id: str) -> dict:
    """从旧study.db读取某会话的所有消息"""
    db_path = Path(__file__).resolve().parents[2] / "data" / "study.db"
    if not db_path.exists():
        return {"messages": []}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM interactions WHERE session_id=? ORDER BY ts ASC", (session_id,))
    rows = cur.fetchall()
    messages = []
    for r in rows:
        messages.append({
            "id": r["id"],
            "question": r["question"],
            "answer": r["answer"],
            "intent": r["intent"],
            "hit": r["hit"],
            "ts": r["ts"],
            "time_str": time.strftime("%H:%M", time.localtime(r["ts"])) if r["ts"] else "",
        })
    conn.close()
    return {"messages": messages}


@router.delete("/sessions/{session_id}/history", summary="删除历史会话")
def delete_history_session(session_id: str) -> dict:
    """从旧study.db删除历史会话及其消息"""
    db_path = Path(__file__).resolve().parents[2] / "data" / "study.db"
    if not db_path.exists():
        return {"status": "ok"}
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("DELETE FROM interactions WHERE session_id=?", (session_id,))
    cur.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "session_id": session_id}
