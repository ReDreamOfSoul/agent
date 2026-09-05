# -*- coding: utf-8 -*-
"""会话与状态管理

对应设计文档「多轮对话与状态管理」：会话级上下文、追问状态机、会话生命周期。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Turn:
    """一轮对话记录"""
    question: str
    answer: str
    intent: str = "knowledge"
    hit_knowledge_base: bool = False
    source_chunks: list[dict] = field(default_factory=list)
    ts: float = field(default_factory=time.time)


class SessionManager:
    """会话管理器：内存态多轮上下文（生产可替换为 Redis 等）"""

    def __init__(self, timeout_minutes: float = 30.0, max_turns: int = 10):
        self.timeout_seconds = timeout_minutes * 60.0
        self.max_turns = max_turns
        self._sessions: dict[str, list[Turn]] = {}
        # 追问状态机：session_id → 上一轮生成、等待学生作答的引导式追问
        self._pending_followup: dict[str, str] = {}

    def create(self) -> str:
        session_id = uuid.uuid4().hex[:16]
        self._sessions[session_id] = []
        return session_id

    def get_turns(self, session_id: str) -> list[Turn]:
        return self._sessions.get(session_id, [])

    def append(self, session_id: str, turn: Turn) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append(turn)
        # 只保留最近 N 轮，控制上下文窗口
        if len(self._sessions[session_id]) > self.max_turns:
            self._sessions[session_id] = self._sessions[session_id][-self.max_turns:]

    def is_valid(self, session_id: str) -> bool:
        """会话是否有效（存在且未超时）"""
        turns = self._sessions.get(session_id)
        if not turns:
            return False
        if time.time() - turns[-1].ts > self.timeout_seconds:
            self._sessions.pop(session_id, None)
            self._pending_followup.pop(session_id, None)
            return False
        return True

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._pending_followup.pop(session_id, None)

    def recent_messages(self, session_id: str, max_rounds: int = 3) -> list[dict]:
        """最近几轮对话的 messages 格式（user/assistant 交替），供 LLM 解析指代"""
        turns = self._sessions.get(session_id, [])
        messages: list[dict] = []
        for t in turns[-max_rounds:]:
            messages.append({"role": "user", "content": t.question})
            messages.append({"role": "assistant", "content": t.answer[:120]})
        return messages

    # ---------- 追问状态机 ----------
    def set_pending_followup(self, session_id: str, followup: str) -> None:
        """记录本轮生成的引导式追问，等待学生作答"""
        if followup.strip():
            self._pending_followup[session_id] = followup.strip()

    def pop_pending_followup(self, session_id: str) -> str:
        """取出并清除待回答的追问（有则说明本轮输入是对追问的回答）"""
        return self._pending_followup.pop(session_id, "")
