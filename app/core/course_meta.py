# -*- coding: utf-8 -*-
"""课程元信息模块

保存与知识点检索无关的课程级信息（课程名称、授课教师、教材、学分学时、
考核方式、先修课程、教学大纲十章内容等），供智能体回答"课程相关"问题时
直接引用，避免污染知识点检索排序。

元信息来源：data/course_meta.json（由教学大纲提取整理，可人工维护）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.course_meta")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_META_PATH = PROJECT_ROOT / "data" / "course_meta.json"


class CourseMeta:
    """课程元信息加载与回答生成"""

    def __init__(self, meta_path: str | Path | None = None):
        self.meta_path = Path(meta_path) if meta_path else DEFAULT_META_PATH
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.meta_path.exists():
            logger.warning("课程元信息文件不存在: %s", self.meta_path)
            self._data = {}
            return
        try:
            self._data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            logger.info("课程元信息加载完成：%s（%d 章）",
                        self._data.get("course_name_cn", "?"),
                        len(self._data.get("chapters", [])))
        except Exception as exc:  # noqa: BLE001
            logger.error("课程元信息加载失败: %s", exc)
            self._data = {}

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def get_chapter(self, name: str) -> dict[str, Any] | None:
        """按章节名（支持模糊匹配）返回章节元信息"""
        if not name:
            return None
        key = name.replace(" ", "")
        for ch in self._data.get("chapters", []):
            ch_name = ch.get("name", "").replace(" ", "")
            if key in ch_name or ch_name in key:
                return ch
        return None

    def summary(self, question: str = "") -> str:
        """将课程元信息格式化为上下文文本（供 LLM 生成回答）

        若传入问题，会根据问题关键词重点突出相关字段；否则返回完整摘要。
        """
        d = self._data
        if not d:
            return "（课程元信息未配置）"

        q = (question or "").strip()
        lines: list[str] = []

        # 基本信息（始终包含）
        lines.append(f"课程名称：{d.get('course_name_cn', '')}（{d.get('course_name_en', '')}）")
        lines.append(f"课程代码：{d.get('course_code', '')}")
        lines.append(f"开课单位：{d.get('department', '')}")
        lines.append(f"适用专业：{d.get('major', '')}")
        lines.append(f"课程负责人/任课教师：{d.get('course_leader', '')}（专业负责人：{d.get('professional_leader', '')}）")
        lines.append(f"学时/学分：{d.get('hours', '')}学时 / {d.get('credits', '')}学分（授课{d.get('hours_breakdown', {}).get('lecture', 0)}学时）")
        lines.append(f"课程性质：{d.get('course_nature', '')}")

        # 教材（问题含教材/书/参考 时重点）
        tb = d.get("textbook", {})
        if tb:
            lines.append(f"选用教材：《{tb.get('name', '')}》，{tb.get('authors', '')}，{tb.get('publisher', '')}{tb.get('year', '')}，ISBN {tb.get('isbn', '')}")
        refs = d.get("references", [])
        if refs and ("参考" in q or "教材" in q or "书" in q or not q):
            ref_str = "；".join(f"《{r.get('name', '')}》（{r.get('authors', '')}，{r.get('publisher', '')}{r.get('year', '')}）" for r in refs)
            lines.append(f"主要参考书：{ref_str}")

        # 考核
        if "考核" in q or "成绩" in q or "考试" in q or "评分" in q or not q:
            lines.append(f"考核方式：{d.get('assessment', '')}")
            lines.append(f"考核说明：{d.get('assessment_detail', '')}")

        # 先修课程
        if "先修" in q or "前置" in q or "基础课" in q or not q:
            lines.append(f"先修课程：{'、'.join(d.get('prerequisites', []))}")

        # 课程说明与目标
        if "目标" in q or "培养" in q or "目的" in q or "课程介绍" in q or "简介" in q or not q:
            lines.append(f"课程说明：{d.get('course_description', '')}")
            for g in d.get("course_goals", []):
                lines.append(f"课程目标{g.get('id', '')}：{g.get('description', '')}")

        # 教学大纲十章（问题含大纲/章节/教学内容/第几章 时包含）
        if ("大纲" in q or "章节" in q or "教学内容" in q or "章" in q
                or "学时" in q or "目录" in q or not q):
            ch_lines = []
            for ch in d.get("chapters", []):
                topics = "、".join(ch.get("topics", []))
                ch_lines.append(f"第{ch.get('no', '')}章 {ch.get('name', '')}（{ch.get('hours', '')}学时）：{topics}")
            lines.append("教学大纲：" + "；".join(ch_lines))

        return "\n".join(lines)

    def answer(self, question: str, llm: Any, history: list[dict] | None = None) -> str:
        """基于课程元信息生成回答（复用 LLM 的知识库整合生成接口）

        将元信息摘要作为唯一上下文片段，LLM 据此回答课程相关问题。
        """
        context = self.summary(question)
        try:
            raw = llm.generate_with_kb(question, [context], history=history or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("课程元信息 LLM 生成失败，回退摘要: %s", exc)
            raw = context
        # 简单后处理：去掉可能的"根据资料"等前缀
        answer = raw.strip()
        return answer
