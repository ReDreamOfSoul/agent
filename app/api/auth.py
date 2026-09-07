# -*- coding: utf-8 -*-
"""用户认证 API：注册、登录、用户信息、修改密码、用户列表"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.auth import (
    create_token, get_current_user, hash_password, verify_password, require_role,
)
from ..db.database import get_db
from ..db.models import User, Enrollment, Course

router = APIRouter(prefix="/api/auth", tags=["认证"])


# ============================================================
# Pydantic 模型
# ============================================================
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    real_name: str = ""
    email: str = ""
    student_id: str = ""
    class_name: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    real_name: str
    email: str
    student_id: str
    teacher_id: str
    class_name: str
    avatar_url: str
    is_active: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ============================================================
# 工具函数
# ============================================================
def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id, username=user.username, role=user.role,
        real_name=user.real_name, email=user.email,
        student_id=user.student_id, teacher_id=user.teacher_id,
        class_name=user.class_name, avatar_url=user.avatar_url,
        is_active=user.is_active, created_at=user.created_at,
    )


# ============================================================
# 接口
# ============================================================
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """学生注册"""
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role="student",
        real_name=req.real_name,
        email=req.email,
        student_id=req.student_id,
        class_name=req.class_name,
    )
    db.add(user)
    db.flush()
    # 自动选默认课程
    course = db.query(Course).filter(Course.course_code == "L7043").first()
    if course:
        db.add(Enrollment(student_id=user.id, course_id=course.id, status="active"))
    db.commit()
    db.refresh(user)
    return _user_to_response(user)


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """登录（学生/教师/管理员通用）"""
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    token = create_token(user.id, user.username, user.role)
    return LoginResponse(access_token=token, user=_user_to_response(user))


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return _user_to_response(user)


@router.put("/password")
def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改密码"""
    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"message": "密码修改成功"}


@router.get("/users")
def list_users(
    role: str | None = None,
    user: User = Depends(require_role("teacher", "admin")),
    db: Session = Depends(get_db),
):
    """用户列表（教师/管理员可查看，可按角色筛选）"""
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    users = query.order_by(User.id).all()
    return [_user_to_response(u) for u in users]
