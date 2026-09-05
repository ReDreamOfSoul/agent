# -*- coding: utf-8 -*-
"""学情数据库（SQLite）

对应设计文档第 8 章「学情分析与数据设计」：
- 数据采集：交互数据、测评数据、学习行为数据；
- 学情指标：活跃度、知识点覆盖、掌握度、薄弱点、投入度；
- 提供教师端 /v1/insight 的聚合查询。
"""
from __future__ import annotations

import json
import logging
import threading
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("agent.study")


class StudyStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions(
                    session_id TEXT PRIMARY KEY,
                    created_at REAL,
                    last_active REAL,
                    turn_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS interactions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    ts REAL,
                    intent TEXT,
                    question TEXT,
                    answer TEXT,
                    hit INTEGER,
                    best_score REAL,
                    generation_mode TEXT,
                    retrieval_mode TEXT,
                    followup_question TEXT
                );
                CREATE TABLE IF NOT EXISTS homework(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    ts REAL,
                    question_text TEXT,
                    student_answer TEXT,
                    score REAL,
                    feedback TEXT,
                    matched_source TEXT
                );
                CREATE TABLE IF NOT EXISTS feedback(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    ts REAL,
                    question TEXT,
                    rating INTEGER,
                    comment TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_interactions_session ON interactions(session_id);
                CREATE INDEX IF NOT EXISTS idx_interactions_ts ON interactions(ts);
                """
            )

    # ---------- 写入 ----------
    def touch_session(self, session_id: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(session_id, created_at, last_active, turn_count) "
                "VALUES(?,?,?,0) ON CONFLICT(session_id) DO UPDATE SET last_active=excluded.last_active",
                (session_id, now, now),
            )

    def bump_turns(self, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET turn_count=turn_count+1, last_active=? WHERE session_id=?",
                (time.time(), session_id),
            )

    def record_interaction(self, session_id: str, *, intent: str, question: str, answer: str,
                           hit: bool, best_score: float, generation_mode: str,
                           retrieval_mode: str, followup_question: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO interactions(session_id, ts, intent, question, answer, hit, "
                "best_score, generation_mode, retrieval_mode, followup_question) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (session_id, time.time(), intent, question, answer, int(hit),
                 best_score, generation_mode, retrieval_mode, followup_question),
            )
            self.bump_turns(session_id)

    def record_homework(self, session_id: str, *, question_text: str, student_answer: str,
                        score: float, feedback: str, matched_source: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO homework(session_id, ts, question_text, student_answer, score, "
                "feedback, matched_source) VALUES(?,?,?,?,?,?,?)",
                (session_id, time.time(), question_text, student_answer, score, feedback, matched_source),
            )

    def record_feedback(self, session_id: str, *, question: str, rating: int, comment: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO feedback(session_id, ts, question, rating, comment) VALUES(?,?,?,?,?)",
                (session_id, time.time(), question, int(rating), comment),
            )

    # ---------- 查询 ----------
    def get_insight(self, days: int = 30) -> dict:
        """教师端学情汇总（最近 days 天）"""
        since = time.time() - days * 86400
        with self._lock, self._conn:
            total_sessions = self._conn.execute(
                "SELECT COUNT(*) c FROM sessions WHERE created_at >= ?", (since,)).fetchone()["c"]
            total_questions = self._conn.execute(
                "SELECT COUNT(*) c FROM interactions WHERE ts >= ?", (since,)).fetchone()["c"]
            hit_rate = self._conn.execute(
                "SELECT AVG(hit) r FROM interactions WHERE ts >= ?", (since,)).fetchone()["r"]
            intent_dist = [
                dict(r) for r in self._conn.execute(
                    "SELECT intent, COUNT(*) c FROM interactions WHERE ts >= ? "
                    "GROUP BY intent ORDER BY c DESC", (since,)).fetchall()
            ]
            # 薄弱点：作业得分低于 0.6 的题目来源聚类
            weak = [
                dict(r) for r in self._conn.execute(
                    "SELECT matched_source, COUNT(*) c, AVG(score) avg_score FROM homework "
                    "WHERE ts >= ? AND score < 0.6 GROUP BY matched_source "
                    "ORDER BY c DESC LIMIT 10", (since,)).fetchall()
            ]
            daily = [
                {"date": time.strftime("%Y-%m-%d", time.localtime(r["day"])), "questions": r["c"]}
                for r in self._conn.execute(
                    "SELECT CAST(ts/86400 AS INT)*86400 AS day, COUNT(*) c FROM interactions "
                    "WHERE ts >= ? GROUP BY day ORDER BY day", (since,)).fetchall()
            ]
            feedback_stats = self._conn.execute(
                "SELECT COUNT(*) c, AVG(rating) avg_rating FROM feedback WHERE ts >= ?", (since,)).fetchone()
        return {
            "days": days,
            "total_sessions": total_sessions,
            "total_questions": total_questions,
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "intent_distribution": intent_dist,
            "weak_points": weak,
            "daily_questions": daily,
            "feedback": {
                "count": feedback_stats["c"],
                "avg_rating": round(feedback_stats["avg_rating"], 2) if feedback_stats["avg_rating"] is not None else None,
            },
        }

    # ---------- 学生个人：自我评估 / 推荐依据 ----------
    def get_session_summary(self, session_id: str) -> dict:
        """单个会话（学生）的学习画像：提问、命中、作业得分与薄弱点"""
        with self._lock, self._conn:
            inter = self._conn.execute(
                "SELECT COUNT(*) c, AVG(hit) hr FROM interactions WHERE session_id=?",
                (session_id,)).fetchone()
            missed = [dict(r) for r in self._conn.execute(
                "SELECT question, best_score FROM interactions WHERE session_id=? AND hit=0 "
                "ORDER BY ts DESC LIMIT 10", (session_id,)).fetchall()]
            hw = self._conn.execute(
                "SELECT COUNT(*) c, AVG(score) avg_s FROM homework WHERE session_id=?",
                (session_id,)).fetchone()
            weak_hw = [dict(r) for r in self._conn.execute(
                "SELECT question_text, score, matched_source FROM homework "
                "WHERE session_id=? AND score < 0.6 ORDER BY ts DESC LIMIT 10", (session_id,)).fetchall()]
        return {
            "session_id": session_id,
            "total_questions": inter["c"],
            "hit_rate": round(inter["hr"], 4) if inter["hr"] is not None else None,
            "missed_questions": missed,
            "homework_count": hw["c"],
            "homework_avg_score": round(hw["avg_s"], 2) if hw["avg_s"] is not None else None,
            "weak_homework": weak_hw,
        }

    # ---------- 教师端：明细导出 ----------
    def export_rows(self, table: str, since: float) -> tuple[list[str], list[tuple]]:
        """导出指定表最近 since 时间戳后的明细，返回 (列名, 行数据)"""
        allowed = {
            "interactions": ("交互记录", ["ts", "session_id", "intent", "question", "answer",
                                         "hit", "best_score", "generation_mode", "retrieval_mode",
                                         "followup_question"]),
            "homework": ("作业批阅", ["ts", "session_id", "question_text", "student_answer",
                                     "score", "feedback", "matched_source"]),
            "feedback": ("学生反馈", ["ts", "session_id", "question", "rating", "comment"]),
        }
        if table not in allowed:
            raise ValueError(f"不支持导出的表: {table}（可选 {', '.join(allowed)}）")
        _, cols = allowed[table]
        with self._lock, self._conn:
            rows = self._conn.execute(
                f"SELECT {', '.join(cols)} FROM {table} WHERE ts >= ? ORDER BY ts", (since,)
            ).fetchall()
        return cols, [tuple(r[c] for c in cols) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __del__(self):  # pragma: no cover
        try:
            self._conn.close()
        except Exception:
            pass
