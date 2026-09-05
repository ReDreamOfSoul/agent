# -*- coding: utf-8 -*-
"""作业批阅接口：POST /v1/homework/judge

对应设计文档「作业提交—自动批阅—反馈推送」流程。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.session import SessionManager
from ..core.workflow import WorkflowEngine
from .deps import get_sessions, get_workflow

router = APIRouter(prefix="/v1", tags=["作业"])


class HomeworkJudgeRequest(BaseModel):
    session_id: str | None = Field(None, description="会话ID，可为空")
    question_text: str = Field(..., min_length=1, max_length=2000, description="题目文本")
    student_answer: str = Field(..., min_length=1, max_length=5000, description="学生作答")


class HomeworkJudgeResponse(BaseModel):
    score: float
    feedback: str
    matched_source: str
    matched_question: str
    reference: str | None
    mode: str


@router.post("/homework/judge", response_model=HomeworkJudgeResponse, summary="作业自动批阅")
def judge(req: HomeworkJudgeRequest, workflow: WorkflowEngine = Depends(get_workflow),
          sessions: SessionManager = Depends(get_sessions)) -> HomeworkJudgeResponse:
    session_id = req.session_id or sessions.create()
    try:
        result = workflow.homework(session_id, req.question_text, req.student_answer)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"批阅服务异常: {exc}") from exc
    return HomeworkJudgeResponse(
        score=result["score"], feedback=result["feedback"],
        matched_source=result["matched_source"], matched_question=result["matched_question"],
        reference=result["reference"], mode=result["mode"],
    )
