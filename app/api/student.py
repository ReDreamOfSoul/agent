# -*- coding: utf-8 -*-
"""学生端接口：自我评估 GET /v1/self-assessment、个性化学习推荐 GET /v1/recommend"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..core.graph import CHAPTER_SEQ, graph_query
from ..core.study_store import StudyStore
from .deps import get_study

router = APIRouter(prefix="/v1", tags=["学生"])


@router.get("/self-assessment", summary="学生自我评估（个人学习画像）")
def self_assessment(session_id: str = Query(..., min_length=1, description="会话ID"),
                    study: StudyStore = Depends(get_study)) -> dict:
    return study.get_session_summary(session_id)


@router.get("/recommend", summary="个性化学习推荐")
def recommend(session_id: str = Query(..., min_length=1, description="会话ID"),
              study: StudyStore = Depends(get_study)) -> dict:
    """基于个人学习画像生成推荐：未命中问题→知识补强；低分作业→前置章节复习"""
    s = study.get_session_summary(session_id)
    recs: list[dict] = []

    for m in s["missed_questions"][:3]:
        recs.append({
            "type": "知识补强",
            "title": m["question"][:40],
            "reason": "该问题未在课程知识库命中，建议更换表述重新提问，或就该方向向任课教师请教。",
            "action": "chat",
        })

    for w in s["weak_homework"][:3]:
        g = graph_query(w["matched_source"] or "", "prerequisite")
        pre = g["related"][0]["name"] if g["related"] else None
        recs.append({
            "type": "习题订正",
            "title": w["question_text"][:40],
            "reason": f"该题得分 {w['score']:.0%}（来源《{w['matched_source']}》），"
                      + (f"建议先复习前置章节「{pre}」，再回到习题自测重做本题。" if pre
                         else "建议复习相关章节后重做本题。"),
            "action": "exercise",
            "chapter": pre,
        })

    if not recs:
        recs.append({
            "type": "学习建议",
            "title": "当前掌握情况良好",
            "reason": "问答命中率与自测得分表现良好，建议按知识图谱顺序推进后续章节的学习与自测。",
            "action": "graph",
            "chapter": CHAPTER_SEQ[0],
        })
    return {"session_id": session_id, "recommendations": recs}
