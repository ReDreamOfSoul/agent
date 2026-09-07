# -*- coding: utf-8 -*-
"""学情记录 API：学习画像、做题记录、视频观看、排行榜、公告"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from ..core.auth import get_current_user, require_role
from ..db.database import get_db
from ..db.models import (
    User, ExerciseRecord, VideoWatch, ResourceDownload,
    StudyProfile, Announcement, Interaction,
)

router = APIRouter(prefix="/api/study", tags=["学情"])


class ExerciseRecordRequest(BaseModel):
    exercise_id: str = ""
    chapter: str = ""
    question_text: str = ""
    student_answer: str = ""
    correct_answer: str = ""
    score: float = 0.0
    is_correct: bool = False


class VideoWatchRequest(BaseModel):
    video_id: str
    chapter: str = ""
    watch_duration: int = 0
    progress: float = 0.0


class AnnouncementCreate(BaseModel):
    course_id: int | None = None
    title: str = Field(min_length=1, max_length=256)
    content: str = ""
    priority: str = "normal"


@router.get("/profile")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取当前用户学情画像"""
    profile = db.query(StudyProfile).filter(StudyProfile.user_id == user.id).first()

    # 实时统计
    total_questions = db.query(Interaction).filter(Interaction.user_id == user.id).count()
    total_exercises = db.query(ExerciseRecord).filter(ExerciseRecord.user_id == user.id).count()
    correct_count = db.query(ExerciseRecord).filter(
        ExerciseRecord.user_id == user.id, ExerciseRecord.is_correct.is_(True)
    ).count()
    correct_rate = round(correct_count / total_exercises * 100, 1) if total_exercises > 0 else 0.0

    # 各章节做题统计
    chapter_stats = {}
    rows = db.query(
        ExerciseRecord.chapter,
        func.count(ExerciseRecord.id).label("total"),
    ).filter(ExerciseRecord.user_id == user.id).group_by(ExerciseRecord.chapter).all()
    for row in rows:
        if row.chapter:
            ch_correct = db.query(ExerciseRecord).filter(
                ExerciseRecord.user_id == user.id,
                ExerciseRecord.chapter == row.chapter,
                ExerciseRecord.is_correct.is_(True),
            ).count()
            chapter_stats[row.chapter] = {
                "total": row.total, "correct": ch_correct,
                "rate": round(ch_correct / row.total * 100, 1) if row.total else 0,
            }

    # 视频观看统计
    video_count = db.query(VideoWatch).filter(VideoWatch.user_id == user.id).count()
    total_watch_seconds = db.query(func.coalesce(func.sum(VideoWatch.watch_duration), 0)).filter(
        VideoWatch.user_id == user.id
    ).scalar()

    return {
        "user_id": user.id, "username": user.username, "real_name": user.real_name,
        "role": user.role,
        "total_questions": total_questions,
        "total_exercises": total_exercises,
        "correct_count": correct_count,
        "correct_rate": correct_rate,
        "chapter_stats": chapter_stats,
        "video_count": video_count,
        "total_watch_minutes": round(total_watch_seconds / 60, 1),
        "last_study_at": profile.last_study_at.isoformat() if profile and profile.last_study_at else None,
    }


@router.get("/exercises")
def list_exercises(
    chapter: str | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """做题记录列表"""
    query = db.query(ExerciseRecord).filter(ExerciseRecord.user_id == user.id)
    if chapter:
        query = query.filter(ExerciseRecord.chapter == chapter)
    records = query.order_by(ExerciseRecord.created_at.desc()).limit(limit).all()
    return [{
        "id": r.id, "chapter": r.chapter, "question_text": r.question_text[:200],
        "student_answer": r.student_answer[:200], "score": r.score,
        "is_correct": r.is_correct, "attempt_count": r.attempt_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in records]


@router.post("/exercises")
def record_exercise(
    req: ExerciseRecordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """记录做题结果"""
    record = ExerciseRecord(
        user_id=user.id, exercise_id=req.exercise_id, chapter=req.chapter,
        question_text=req.question_text, student_answer=req.student_answer,
        correct_answer=req.correct_answer, score=req.score, is_correct=req.is_correct,
    )
    db.add(record)
    # 更新画像
    profile = db.query(StudyProfile).filter(StudyProfile.user_id == user.id).first()
    if not profile:
        profile = StudyProfile(user_id=user.id)
        db.add(profile)
    profile.last_study_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "做题记录已保存", "id": record.id}


@router.get("/videos")
def list_video_watch(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """视频观看记录"""
    records = db.query(VideoWatch).filter(VideoWatch.user_id == user.id).order_by(
        VideoWatch.last_watched_at.desc()
    ).all()
    return [{
        "id": r.id, "video_id": r.video_id, "chapter": r.chapter,
        "watch_duration": r.watch_duration, "progress": r.progress,
        "last_watched_at": r.last_watched_at.isoformat() if r.last_watched_at else None,
    } for r in records]


@router.post("/videos")
def record_video_watch(
    req: VideoWatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """记录视频观看（同一视频累加时长）"""
    record = db.query(VideoWatch).filter(
        VideoWatch.user_id == user.id, VideoWatch.video_id == req.video_id
    ).first()
    if record:
        record.watch_duration += req.watch_duration
        record.progress = max(record.progress, req.progress)
        record.chapter = req.chapter or record.chapter
    else:
        record = VideoWatch(
            user_id=user.id, video_id=req.video_id, chapter=req.chapter,
            watch_duration=req.watch_duration, progress=req.progress,
        )
        db.add(record)
    db.commit()
    return {"message": "观看记录已保存"}


@router.get("/ranking")
def get_ranking(
    limit: int = 10,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """学习排行榜（按提问数+做题数综合）"""
    rows = db.query(
        User.id, User.username, User.real_name, User.class_name,
        func.count(Interaction.id).label("question_count"),
    ).outerjoin(Interaction, Interaction.user_id == User.id).filter(
        User.role == "student", User.is_active == True
    ).group_by(User.id).order_by(desc("question_count")).limit(limit).all()

    ranking = []
    for row in rows:
        exercise_count = db.query(ExerciseRecord).filter(ExerciseRecord.user_id == row.id).count()
        ranking.append({
            "rank": 0, "user_id": row.id, "username": row.username,
            "real_name": row.real_name, "class_name": row.class_name,
            "question_count": row.question_count, "exercise_count": exercise_count,
            "total_score": row.question_count * 2 + exercise_count * 3,
        })
    ranking.sort(key=lambda x: x["total_score"], reverse=True)
    for i, item in enumerate(ranking):
        item["rank"] = i + 1
    return ranking


# ============================================================
# 公告
# ============================================================
@router.get("/announcements")
def list_announcements(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """公告列表（只看已发布的）"""
    announcements = db.query(Announcement).filter(
        Announcement.is_published == True
    ).order_by(Announcement.published_at.desc()).limit(20).all()
    return [{
        "id": a.id, "title": a.title, "content": a.content,
        "priority": a.priority, "published_at": a.published_at.isoformat() if a.published_at else None,
    } for a in announcements]


@router.post("/announcements", status_code=201)
def create_announcement(
    req: AnnouncementCreate,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师发布公告"""
    announcement = Announcement(
        course_id=req.course_id, teacher_id=user.id,
        title=req.title, content=req.content, priority=req.priority,
        is_published=True, published_at=datetime.now(timezone.utc),
    )
    db.add(announcement)
    db.commit()
    return {"message": "公告已发布", "id": announcement.id}


@router.get("/teacher/overview")
def teacher_overview(
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师端总览数据"""
    student_count = db.query(User).filter(User.role == "student", User.is_active == True).count()
    total_questions = db.query(Interaction).count()
    total_exercises = db.query(ExerciseRecord).count()
    assignment_count = db.query(__import__("app.db.models", fromlist=["Assignment"]).Assignment).filter(
        __import__("app.db.models", fromlist=["Assignment"]).Assignment.teacher_id == user.id
    ).count()

    # 活跃学生（最近7天有交互）
    from datetime import timedelta
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    active_students = db.query(User).join(
        Interaction, Interaction.user_id == User.id
    ).filter(
        User.role == "student",
        Interaction.created_at >= seven_days_ago,
    ).distinct().count()

    return {
        "student_count": student_count,
        "active_students": active_students,
        "total_questions": total_questions,
        "total_exercises": total_exercises,
        "assignment_count": assignment_count,
    }
