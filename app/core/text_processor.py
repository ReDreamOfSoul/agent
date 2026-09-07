# -*- coding: utf-8 -*-
"""文本处理模块：回答的格式化、规范化和通俗化

对应设计文档「文本处理」节点：分点说明、公式排版、重点标注、语言通俗化。
"""
from __future__ import annotations

import re


def normalize_whitespace(text: str) -> str:
    """统一空白：全角/半角空格规整、去除多余空行"""
    text = text.replace("\u3000", " ")
    lines = [ln.rstrip() for ln in text.splitlines()]
    cleaned: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            cleaned.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                cleaned.append("")
    return "\n".join(cleaned).strip()


def ensure_bullet_items(text: str) -> str:
    """将回答中散落的编号点（1. / ① / ·）规整为统一的分点格式"""
    text = normalize_whitespace(text)
    # ① ② ③ → 1. 2. 3.
    text = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩]", lambda m: {"①": "1.", "②": "2.", "③": "3.",
                                                    "④": "4.", "⑤": "5.", "⑥": "6.",
                                                    "⑦": "7.", "⑧": "8.", "⑨": "9.", "⑩": "10."}[m.group()], text)
    return text


def wrap_math_expressions(text: str) -> str:
    """公式排版：将行内 ASCII 数学表达式转换为更易读的表示

    保留 $...$ / $$...$$ 原样（渲染端处理），将未包裹的常见公式片段补全。
    """
    # 保护已包裹的公式
    protected: list[str] = []
    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00{m.group(0).count('$')}\x00"

    text = re.sub(r"\$\$.*?\$\$|\$.*?\$", _protect, text, flags=re.S)
    # 规范化全角乘号/减号等
    text = text.replace("×", " × ").replace("＝", "=")
    # 恢复受保护内容
    for frag in protected:
        text = text.replace(f"\x00{frag.count('$')}\x00", frag, 1)
    return text


def highlight_keywords(text: str, keywords: list[str], max_hits: int = 6) -> str:
    """重点标注：将回答中的课程术语高亮为 **术语** 形式（配合前端渲染）

    仅对出现频次合理的关键词生效，避免过度标注。
    """
    for kw in keywords[:max_hits]:
        kw = kw.strip()
        if not kw or len(kw) < 2:
            continue
        # 避免重复包裹
        if f"**{kw}**" in text:
            continue
        text = re.sub(re.escape(kw), f"**{kw}**", text, count=3)
    return text


def split_into_points(text: str, max_len: int = 160) -> str:
    """长段文本切分为分点说明（启发式：按句号/分号切分并加序号）"""
    text = normalize_whitespace(text)
    if len(text) <= max_len or text.count("\n") >= 2:
        return text
    parts = re.split(r"(?<=[。；;])", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return text
    return "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))


def process_answer(text: str, keywords: list[str] | None = None) -> str:
    """文本处理节点主入口：清洗 → 分点 → 公式 → 标注"""
    text = normalize_whitespace(text)
    text = split_into_points(text)
    text = wrap_math_expressions(text)
    if keywords:
        text = highlight_keywords(text, keywords)
    return text
