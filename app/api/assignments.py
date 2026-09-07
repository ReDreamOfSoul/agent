# -*- coding: utf-8 -*-
"""作业管理 API：教师发布作业、学生提交、教师批改"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.auth import get_current_user, require_role
from ..db.database import get_db
from ..db.models import User, Course, Assignment, Submission, Enrollment

router = APIRouter(prefix="/api/assignments", tags=["作业"])


class AssignmentCreate(BaseModel):
    course_id: int
    title: str = Field(min_length=1, max_length=256)
    description: str = ""
    content: str = ""
    total_score: float = 100.0
    due_date: datetime | None = None
    status: str = "draft"  # draft / published


class AssignmentUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    content: str | None = None
    total_score: float | None = None
    due_date: datetime | None = None
    status: str | None = None


class SubmitRequest(BaseModel):
    content: str


class GradeRequest(BaseModel):
    score: float
    teacher_feedback: str = ""


def _assignment_to_dict(a: Assignment, db: Session, user: User) -> dict:
    submission = None
    if user.role == "student":
        submission = db.query(Submission).filter(
            Submission.assignment_id == a.id, Submission.student_id == user.id
        ).first()
    return {
        "id": a.id, "course_id": a.course_id, "teacher_id": a.teacher_id,
        "title": a.title, "description": a.description, "content": a.content,
        "total_score": a.total_score, "due_date": a.due_date.isoformat() if a.due_date else None,
        "status": a.status, "created_at": a.created_at.isoformat() if a.created_at else None,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "my_submission": {
            "id": submission.id, "content": submission.content,
            "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None,
            "score": submission.score, "teacher_feedback": submission.teacher_feedback,
            "graded_at": submission.graded_at.isoformat() if submission.graded_at else None,
        } if submission else None,
    }


@router.get("")
def list_assignments(
    course_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """作业列表：学生看已发布的，教师看自己创建的全部"""
    query = db.query(Assignment)
    if user.role == "student":
        query = query.filter(Assignment.status == "published")
        if course_id:
            query = query.filter(Assignment.course_id == course_id)
    else:
        if course_id:
            query = query.filter(Assignment.course_id == course_id)
        else:
            query = query.filter(Assignment.teacher_id == user.id)
    assignments = query.order_by(Assignment.created_at.desc()).all()
    return [_assignment_to_dict(a, db, user) for a in assignments]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_assignment(
    req: AssignmentCreate,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师创建作业"""
    course = db.query(Course).filter(Course.id == req.course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")
    assignment = Assignment(
        course_id=req.course_id, teacher_id=user.id,
        title=req.title, description=req.description, content=req.content,
        total_score=req.total_score, due_date=req.due_date, status=req.status,
        published_at=datetime.now(timezone.utc) if req.status == "published" else None,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return {"id": assignment.id, "message": "作业创建成功"}


@router.get("/{assignment_id}")
def get_assignment(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """作业详情"""
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")
    if user.role == "student" and assignment.status != "published":
        raise HTTPException(status_code=403, detail="作业未发布")
    return _assignment_to_dict(assignment, db, user)


@router.put("/{assignment_id}")
def update_assignment(
    assignment_id: int,
    req: AssignmentUpdate,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师更新作业"""
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")
    if assignment.teacher_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权修改此作业")
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(assignment, field, value)
    if req.status == "published" and not assignment.published_at:
        assignment.published_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "作业更新成功"}


@router.delete("/{assignment_id}")
def delete_assignment(
    assignment_id: int,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师删除作业"""
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")
    if assignment.teacher_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权删除此作业")
    db.delete(assignment)
    db.commit()
    return {"message": "作业已删除"}


@router.post("/{assignment_id}/submit")
def submit_assignment(
    assignment_id: int,
    req: SubmitRequest,
    user: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
):
    """学生提交作业"""
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment or assignment.status != "published":
        raise HTTPException(status_code=404, detail="作业不存在或未发布")
    existing = db.query(Submission).filter(
        Submission.assignment_id == assignment_id, Submission.student_id == user.id
    ).first()
    if existing:
        existing.content = req.content
        existing.submitted_at = datetime.now(timezone.utc)
        existing.score = None
        existing.teacher_feedback = ""
        existing.graded_at = None
        db.commit()
        return {"message": "作业已重新提交", "submission_id": existing.id}
    submission = Submission(
        assignment_id=assignment_id, student_id=user.id,
        content=req.content, submitted_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return {"message": "作业提交成功", "submission_id": submission.id}


@router.get("/{assignment_id}/submissions")
def list_submissions(
    assignment_id: int,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师查看作业提交列表"""
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")
    submissions = db.query(Submission).filter(Submission.assignment_id == assignment_id).all()
    result = []
    for sub in submissions:
        student = db.query(User).filter(User.id == sub.student_id).first()
        result.append({
            "id": sub.id, "student_id": sub.student_id,
            "student_name": student.real_name if student else "",
            "student_username": student.username if student else "",
            "content": sub.content,
            "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            "score": sub.score, "teacher_feedback": sub.teacher_feedback,
            "graded_at": sub.graded_at.isoformat() if sub.graded_at else None,
        })
    return result


@router.post("/{assignment_id}/grade/{submission_id}")
def grade_submission(
    assignment_id: int,
    submission_id: int,
    req: GradeRequest,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """教师批改作业"""
    submission = db.query(Submission).filter(
        Submission.id == submission_id, Submission.assignment_id == assignment_id
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    submission.score = req.score
    submission.teacher_feedback = req.teacher_feedback
    submission.graded_at = datetime.now(timezone.utc)
    submission.graded_by = user.id
    db.commit()
    return {"message": "批改完成"}
