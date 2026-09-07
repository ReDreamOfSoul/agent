# -*- coding: utf-8 -*-
"""用户认证：密码哈希（sha256+salt）+ JWT token（hmac+base64，零外部依赖）"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import User

# JWT 密钥（从环境变量读取，开发环境用默认值）
JWT_SECRET = os.environ.get("AGENT_JWT_SECRET", "robotics-agent-dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24 * 7  # 7 天

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ============================================================
# 密码哈希
# ============================================================
def hash_password(password: str, salt: str | None = None) -> str:
    """sha256(password + salt)，返回 salt:hash"""
    if salt is None:
        salt = os.urandom(16).hex()
    pw_hash = hashlib.sha256(f"{password}{salt}".encode("utf-8")).hexdigest()
    return f"{salt}${pw_hash}"


def verify_password(password: str, stored: str) -> bool:
    """验证密码"""
    try:
        salt, pw_hash = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(
        hashlib.sha256(f"{password}{salt}".encode("utf-8")).hexdigest(),
        pw_hash,
    )


# ============================================================
# JWT Token（手写 HS256，零依赖）
# ============================================================
def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def create_token(user_id: int, username: str, role: str) -> str:
    """生成 JWT token"""
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_EXPIRE_HOURS * 3600,
    }
    header_b64 = _b64encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}"
    signature = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    signature_b64 = _b64encode(signature)
    return f"{signing_input}.{signature_b64}"


def decode_token(token: str) -> dict[str, Any] | None:
    """解码并验证 JWT token，失败返回 None"""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature_b64), expected_sig):
            return None
        payload = json.loads(_b64decode(payload_b64))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# ============================================================
# FastAPI 依赖：获取当前用户
# ============================================================
def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 token 获取当前用户，未登录抛 401"""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user


def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """可选：已登录返回用户，未登录返回 None"""
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload["sub"])).first()


def require_role(*roles: str):
    """角色权限装饰器依赖"""
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"需要 {roles} 权限")
        return user
    return _checker
