# -*- coding: utf-8 -*-
"""视频课程接口：GET /v1/videos（视频清单，按章节分组）

视频以 manifest.json 形式登记在知识库 video 目录，构建时生成「视频」类型知识块，
本接口从知识库读取清单供前端「视频课程」面板使用。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..rag.knowledge_base import KnowledgeBase
from .deps import get_kb

router = APIRouter(prefix="/v1", tags=["视频课程"])


@router.get("/videos", summary="视频课程清单（按章节分组）")
def videos(kb: KnowledgeBase = Depends(get_kb)) -> dict:
    items: list[dict] = []
    for c in kb.chunks:
        if (c.resource_type or "") != "视频":
            continue
        items.append({
            "id": c.meta.get("video_id", ""),
            "title": c.text.splitlines()[0].replace("【视频课程】", "").strip()
                     if c.text.startswith("【视频课程】") else c.source,
            "chapter": c.chapter or "",
            "video_url": c.video_url or "",
            "description": c.meta.get("description", ""),
        })
    by_chapter: dict[str, list[dict]] = {}
    for it in items:
        by_chapter.setdefault(it["chapter"] or "未分类", []).append(it)
    return {"total": len(items), "by_chapter": by_chapter, "videos": items}
