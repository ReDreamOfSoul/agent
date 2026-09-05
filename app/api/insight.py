# -*- coding: utf-8 -*-
"""学情查询接口：GET /v1/insight

教师端查看班级学情汇总（活跃度、知识点覆盖、掌握度、薄弱点、反馈）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..core.study_store import StudyStore
from .deps import get_study

router = APIRouter(prefix="/v1", tags=["学情"])


@router.get("/insight", summary="学情统计汇总")
def insight(days: int = Query(30, ge=1, le=365), study: StudyStore = Depends(get_study)) -> dict:
    return study.get_insight(days=days)
