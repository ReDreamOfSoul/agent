# -*- coding: utf-8 -*-
"""习题自测接口：GET /v1/exercises

题目来源（两级）：
1. 知识库题库资料中 "题目：…参考答案：…" 结构解析（精确匹配，可客观判分）；
2. 题库题量不足时，由 LLM 基于教材/课件内容动态生成自测题（答案由教师复核模式批阅）。
学生作答后调用既有 POST /v1/homework/judge 完成自动批阅，形成"抽题→作答→评分"闭环。
"""
from __future__ import annotations

import logging
import random
import re

from fastapi import APIRouter, Depends, Query

from ..core.workflow import WorkflowEngine
from ..rag.knowledge_base import KnowledgeBase
from .deps import get_kb, get_workflow

logger = logging.getLogger("agent.api.exercise")
router = APIRouter(prefix="/v1", tags=["习题自测"])

# 题库解析（两种格式兼容）：
# 格式A（课程题库真实格式）："1、题目…\n答案： D" / "2、题目…\n答案： 段落"
# 格式B（通用兼容格式）："题目：…参考答案：…"
_NUM_Q_RE = re.compile(r"(?:^|[\r\n])\s*(\d+)[、．.]")
_ANSWER_MARKERS = ("参考答案：", "参考答案:", "答案：", "答案:", "答：", "答:", "【答案】", "[答案]")
_QA_PATTERN = re.compile(r"题目[：:]\s*(.+?)\s*参考答案[：:]\s*(.+?)(?=\s*题目[：:]|$)", re.S)


def _clean_answer(a: str) -> str:
    """去除空白与斜杠占位符（题库部分题目答案以 / 占位）"""
    return re.sub(r"[\s/\\|]+", "", a or "")


def extract_questions(chunk_text: str) -> list[dict]:
    """从题库文本解析题目与参考答案，支持序号题号与"题目：…参考答案："两种格式"""
    items: list[dict] = []
    marks = list(_NUM_Q_RE.finditer(chunk_text))
    if marks:
        for i, m in enumerate(marks):
            seg = chunk_text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(chunk_text)]
            q_text, ans = seg, ""
            for mk in _ANSWER_MARKERS:
                idx = seg.find(mk)
                if idx >= 0:
                    q_text, ans = seg[:idx].strip(), seg[idx + len(mk):].strip()
                    break
            q_line = q_text.splitlines()[0].strip() if q_text else ""
            if len(q_line) < 4:
                continue
            if not _clean_answer(ans):
                continue
            items.append({"question": q_text, "answer": ans[:300]})
    for m in _QA_PATTERN.finditer(chunk_text):
        q = m.group(1).strip().splitlines()[0].strip()
        a = m.group(2).strip()
        if len(q) >= 4 and _clean_answer(a):
            items.append({"question": q, "answer": a[:300]})
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in items:
        if it["question"] not in seen:
            seen.add(it["question"])
            uniq.append(it)
    return uniq


def detect_question_type(question: str, answer: str) -> dict:
    """识别题型：单选/多选/简答，提取选项（支持同行选项和换行选项）"""
    q = (question or "").strip()
    a = (answer or "").strip()
    # 匹配选项：A、xxx B、xxx C、xxx D、xxx（支持同行和换行，选项前有空格或换行）
    option_pattern = re.compile(r'(?:^|[\s])([A-D])[、．.．]\s*([^A-D\n]{1,80}?)(?=\s*[A-D][、．.．]|$)', re.S)
    options = {}
    for m in option_pattern.finditer(q):
        key = m.group(1)
        val = m.group(2).strip().rstrip('。.，,')
        if val and len(val) >= 1:
            options[key] = val
    # 清理题目文本（去掉选项部分）
    q_clean = re.split(r'\s*[A-D][、．.．]\s', q)[0].strip()
    if not q_clean:
        q_clean = q
    # 判断题型
    ans_clean = re.sub(r'[\s，。、．.]+', '', a).upper()
    if len(options) >= 2:
        # 有选项 → 选择题
        if len(ans_clean) == 1 and ans_clean in 'ABCD':
            qtype = "single"
        elif len(ans_clean) > 1 and all(c in 'ABCD' for c in ans_clean):
            qtype = "multiple"
        else:
            qtype = "single" if len(ans_clean) <= 2 else "short"
        return {"type": qtype, "options": options, "question_clean": q_clean}
    return {"type": "short", "options": {}, "question_clean": q}


def _bank_questions(kb: KnowledgeBase, chapter: str) -> list[dict]:
    """从题库类资源块解析题目（同一文件的多个分块先合并，避免题目与答案被分块截断）"""
    per_source: dict[str, list[str]] = {}
    for c in kb.chunks:
        if "题库" not in (c.resource_type or ""):
            continue
        if chapter and chapter not in (c.chapter or ""):
            continue
        per_source.setdefault(c.source, []).append(c.text)
    pool: list[dict] = []
    for source, texts in per_source.items():
        merged = "\n".join(texts)
        for q in extract_questions(merged):
            q["source"] = source
            q["chapter"] = ""
            q["generated"] = False
            pool.append(q)
    seen: set[str] = set()
    uniq: list[dict] = []
    for q in pool:
        if q["question"] not in seen:
            seen.add(q["question"])
            uniq.append(q)
    return uniq


def _llm_questions(kb: KnowledgeBase, workflow: WorkflowEngine,
                   limit: int, chapter: str) -> list[dict]:
    """题库不足时，抽样教材/课件内容块，由 LLM 动态生成自测题"""
    candidates = [
        c for c in kb.chunks
        if "题库" not in (c.resource_type or "") and len(c.text) >= 120
        and (not chapter or chapter in (c.chapter or ""))
    ]
    if not candidates:
        return []
    random.shuffle(candidates)
    out: list[dict] = []
    for c in candidates:
        if len(out) >= limit:
            break
        try:
            q = workflow.llm.generate_exercise(c.text, c.chapter or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 生成题目失败: %s", exc)
            continue
        if not q:
            continue
        q["source"] = f"AI 生成（基于《{c.source}》" + (f"·{c.chapter}" if c.chapter else "") + "）"
        q["chapter"] = c.chapter
        q["generated"] = True
        out.append(q)
    return out


@router.get("/exercises", summary="抽取自测习题（题库优先，不足时 AI 生成）")
def exercises(limit: int = Query(5, ge=1, le=20, description="返回题数"),
              chapter: str = Query("", description="章节过滤（模糊匹配）"),
              kb: KnowledgeBase = Depends(get_kb),
              workflow: WorkflowEngine = Depends(get_workflow)) -> dict:
    bank = _bank_questions(kb, chapter)
    random.shuffle(bank)
    result = bank[:limit]
    generated_count = 0
    if len(result) < limit:
        need = limit - len(result)
        gen = _llm_questions(kb, workflow, need, chapter)
        result.extend(gen)
        generated_count = len(gen)
    # 给每道题加题型信息
    for ex in result:
        info = detect_question_type(ex.get("question", ""), ex.get("answer", ""))
        ex["qtype"] = info["type"]
        ex["options"] = info["options"]
        if info["question_clean"]:
            ex["question_display"] = info["question_clean"]
        else:
            ex["question_display"] = ex.get("question", "")
    return {
        "total": len(result),
        "bank_total": len(bank),
        "generated": generated_count,
        "exercises": result,
    }
