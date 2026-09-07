# -*- coding: utf-8 -*-
"""课程元信息接口：GET /v1/course/meta

返回课程名称、授课教师、教材、学分学时、考核方式、先修课程、教学大纲十章等
与知识点检索无关的课程级信息。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..core.course_meta import CourseMeta

router = APIRouter(prefix="/v1/course", tags=["课程信息"])

_course_meta = CourseMeta()


@router.get("/meta", summary="课程元信息（课程名称/教师/教材/学分/考核/大纲等）")
def course_meta() -> dict:
    return _course_meta.data
