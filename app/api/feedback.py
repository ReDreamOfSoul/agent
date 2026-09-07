# -*- coding: utf-8 -*-
"""反馈接口：POST /v1/feedback"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.session import SessionManager
from ..core.study_store import StudyStore
from .deps import get_sessions, get_study

router = APIRouter(prefix="/v1", tags=["反馈"])


class FeedbackRequest(BaseModel):
    session_id: str | None = Field(None, description="会话ID")
    question: str = Field(..., description="学生问题（用于关联交互记录）")
    rating: int = Field(1, ge=1, le=5, description="评分 1-5")
    comment: str = Field("", max_length=500, description="补充意见")


@router.post("/feedback", summary="提交答案反馈")
def submit_feedback(req: FeedbackRequest, study: StudyStore = Depends(get_study),
                    sessions: SessionManager = Depends(get_sessions)) -> dict:
    session_id = req.session_id or sessions.create()
    study.record_feedback(session_id, question=req.question, rating=req.rating, comment=req.comment)
    return {"status": "ok", "session_id": session_id}
