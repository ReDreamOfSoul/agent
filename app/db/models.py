# -*- coding: utf-8 -*-
"""数据库 ORM 模型（15 张表）

覆盖：用户系统（学生/教师/管理员）、课程与选课、会话与问答交互、
作业发布与提交批改、做题记录、视频观看、资源下载、学情画像、公告、反馈、登录日志。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer,
    String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# 1. 用户系统
# ============================================================
class User(Base):
    """用户表（学生 / 教师 / 管理员）"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default="student")  # student / teacher / admin
    real_name = Column(String(64), default="")
    email = Column(String(128), default="")
    student_id = Column(String(32), default="")   # 学号
    teacher_id = Column(String(32), default="")   # 工号
    class_name = Column(String(64), default="")    # 班级
    avatar_url = Column(String(512), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    enrollments = relationship("Enrollment", back_populates="student", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    interactions = relationship("Interaction", back_populates="user", cascade="all, delete-orphan")
    exercise_records = relationship("ExerciseRecord", back_populates="user", cascade="all, delete-orphan")
    submissions = relationship("Submission", back_populates="student",
                                foreign_keys="Submission.student_id", cascade="all, delete-orphan")
    assignments_created = relationship("Assignment", back_populates="teacher", foreign_keys="Assignment.teacher_id")
    study_profile = relationship("StudyProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    login_logs = relationship("LoginLog", back_populates="user", cascade="all, delete-orphan")


# ============================================================
# 2. 课程与选课
# ============================================================
class Course(Base):
    """课程表"""
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_code = Column(String(32), unique=True, nullable=False)  # L7043
    course_name = Column(String(128), nullable=False)               # 机器人学导论
    course_name_en = Column(String(128), default="")
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    semester = Column(String(32), default="")
    credit = Column(Float, default=0.0)
    hours = Column(Integer, default=0)
    description = Column(Text, default="")
    textbook = Column(String(256), default="")
    created_at = Column(DateTime, default=_now)

    teacher = relationship("User", foreign_keys=[teacher_id])
    enrollments = relationship("Enrollment", back_populates="course", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="course", cascade="all, delete-orphan")
    announcements = relationship("Announcement", back_populates="course", cascade="all, delete-orphan")


class Enrollment(Base):
    """选课表（学生-课程多对多）"""
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "course_id", name="uq_enroll"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    status = Column(String(16), default="active")  # active / dropped
    enrolled_at = Column(DateTime, default=_now)

    student = relationship("User", back_populates="enrollments")
    course = relationship("Course", back_populates="enrollments")


# ============================================================
# 3. 会话与问答交互
# ============================================================
class Session(Base):
    """会话表（扩展原 sessions，关联用户）"""
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
    title = Column(String(256), default="新对话")
    turn_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)
    last_active = Column(DateTime, default=_now, onupdate=_now)

    user = relationship("User", back_populates="sessions")
    # interactions 关系已移除（会话在旧库管理，无外键）


class Interaction(Base):
    """问答交互表（扩展原 interactions，关联用户）"""
    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)  # 会话在旧study.db管理，不设外键
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    intent = Column(String(32), default="knowledge")
    question = Column(Text, nullable=False)
    answer = Column(Text, default="")
    hit = Column(Boolean, default=False)
    best_score = Column(Float, default=0.0)
    generation_mode = Column(String(32), default="")
    retrieval_mode = Column(String(32), default="")
    followup_question = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now, index=True)

    # session 关系已移除（会话在旧库管理）
    user = relationship("User", back_populates="interactions")
    feedback = relationship("Feedback", back_populates="interaction", uselist=False)


# ============================================================
# 4. 作业系统
# ============================================================
class Assignment(Base):
    """作业表（教师发布）"""
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    content = Column(Text, default="")           # 作业题目/要求
    total_score = Column(Float, default=100.0)
    due_date = Column(DateTime, nullable=True)
    status = Column(String(16), default="draft")  # draft / published / closed
    created_at = Column(DateTime, default=_now)
    published_at = Column(DateTime, nullable=True)

    course = relationship("Course", back_populates="assignments")
    teacher = relationship("User", back_populates="assignments_created", foreign_keys=[teacher_id])
    submissions = relationship("Submission", back_populates="assignment", cascade="all, delete-orphan")


class Submission(Base):
    """作业提交表（学生提交 + 教师批改）"""
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", name="uq_submit"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, default="")           # 提交内容
    submitted_at = Column(DateTime, default=_now)
    score = Column(Float, nullable=True)          # 教师批改分数
    teacher_feedback = Column(Text, default="")
    graded_at = Column(DateTime, nullable=True)
    graded_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    assignment = relationship("Assignment", back_populates="submissions")
    student = relationship("User", back_populates="submissions", foreign_keys=[student_id])
    grader = relationship("User", foreign_keys=[graded_by])


# ============================================================
# 5. 学习行为记录
# ============================================================
class ExerciseRecord(Base):
    """做题记录表"""
    __tablename__ = "exercise_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    exercise_id = Column(String(64), default="")
    chapter = Column(String(128), default="")
    question_text = Column(Text, default="")
    student_answer = Column(Text, default="")
    correct_answer = Column(Text, default="")
    score = Column(Float, default=0.0)
    is_correct = Column(Boolean, default=False)
    attempt_count = Column(Integer, default=1)
    created_at = Column(DateTime, default=_now, index=True)

    user = relationship("User", back_populates="exercise_records")


class VideoWatch(Base):
    """视频观看记录表"""
    __tablename__ = "video_watch"
    __table_args__ = (UniqueConstraint("user_id", "video_id", name="uq_video_watch"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    video_id = Column(String(128), nullable=False)
    chapter = Column(String(128), default="")
    watch_duration = Column(Integer, default=0)   # 秒
    progress = Column(Float, default=0.0)          # 0-100
    last_watched_at = Column(DateTime, default=_now, onupdate=_now)

    user = relationship("User")


class ResourceDownload(Base):
    """资源下载记录表"""
    __tablename__ = "resource_downloads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resource_id = Column(String(128), default="")
    resource_type = Column(String(32), default="")
    resource_title = Column(String(256), default="")
    downloaded_at = Column(DateTime, default=_now)

    user = relationship("User")


# ============================================================
# 6. 学情画像
# ============================================================
class StudyProfile(Base):
    """学情画像表（每用户一条，汇总学习数据）"""
    __tablename__ = "study_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
    total_questions = Column(Integer, default=0)      # 总提问数
    total_exercises = Column(Integer, default=0)      # 总做题数
    correct_count = Column(Integer, default=0)        # 做对题数
    correct_rate = Column(Float, default=0.0)         # 正确率
    study_minutes = Column(Integer, default=0)        # 学习时长（分钟）
    weak_chapters = Column(JSON, default=list)        # 薄弱章节
    strong_chapters = Column(JSON, default=list)      # 掌握章节
    chapter_stats = Column(JSON, default=dict)        # 各章节详细统计
    last_study_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    user = relationship("User", back_populates="study_profile")


# ============================================================
# 7. 公告
# ============================================================
class Announcement(Base):
    """公告表（教师发布课程公告）"""
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(256), nullable=False)
    content = Column(Text, default="")
    priority = Column(String(16), default="normal")   # normal / important
    is_published = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)
    published_at = Column(DateTime, nullable=True)

    course = relationship("Course", back_populates="announcements")
    teacher = relationship("User")


# ============================================================
# 8. 反馈与登录日志
# ============================================================
class Feedback(Base):
    """反馈表（扩展原 feedback，关联用户和交互）"""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    session_id = Column(String(64), default="")
    interaction_id = Column(Integer, ForeignKey("interactions.id"), nullable=True)
    question = Column(Text, default="")
    rating = Column(Integer, default=0)
    comment = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

    user = relationship("User")
    interaction = relationship("Interaction", back_populates="feedback")


class LoginLog(Base):
    """登录日志表"""
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    login_at = Column(DateTime, default=_now)
    ip_address = Column(String(64), default="")
    user_agent = Column(String(512), default="")
    success = Column(Boolean, default=True)

    user = relationship("User", back_populates="login_logs")


# ============================================================
# 索引
# ============================================================
Index("ix_interactions_user_time", Interaction.user_id, Interaction.created_at)
Index("ix_exercise_user_chapter", ExerciseRecord.user_id, ExerciseRecord.chapter)
Index("ix_submissions_student", Submission.student_id)
