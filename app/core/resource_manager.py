# -*- coding: utf-8 -*-
"""课程资源管理器

加载 data/resources.json，根据用户问题匹配对应课程资料（PPT/教材/视频/题库/大纲），
供智能体"要资料"意图返回下载链接。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.resource")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESOURCES_PATH = PROJECT_ROOT / "data" / "resources.json"

# 资源类型关键词映射
_TYPE_KEYWORDS: dict[str, list[str]] = {
    "ppt": ["ppt", "PPT", "课件", "幻灯片", "pptx"],
    "video": ["视频", "录像", "教学视频", "讲课视频"],
    "pdf": ["教材", "课本", "书", "PDF", "pdf", "电子书"],
    "exercise": ["题库", "习题集", "题目集", "练习题"],
    "syllabus": ["大纲", "教学大纲", "课程大纲"],
}

# 章节关键词（用于从问题中提取章节）
_CHAPTER_PATTERNS = [
    (r"第([一二三四五六七八九十\d]+)章", "chapter_num"),
    (r"绪论", "第一章 绪论"),
    (r"本体结构", "第二章 机器人本体结构"),
    (r"运动学", "第三章 机器人运动学"),
    (r"动力学", "第四章 机器人动力学"),
    (r"轨迹规划", "第五章 机器人轨迹规划"),
    (r"控制系统|机器人控制", "第六章 机器人控制系统"),
    (r"编程|机器人语言", "第七章 机器人的编程与语言"),
    (r"工业机器人", "第八章 工业机器人"),
    (r"操纵型", "第九章 操纵型机器人"),
    (r"仿生", "第十章 仿生机器人"),
]

_CN_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CN_NUMS = "零一二三四五六七八九十"


class ResourceManager:
    """课程资源加载与匹配"""

    def __init__(self, resources_path: str | Path | None = None):
        self.resources_path = Path(resources_path) if resources_path else DEFAULT_RESOURCES_PATH
        self._resources: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.resources_path.exists():
            logger.warning("资源清单不存在: %s", self.resources_path)
            self._resources = []
            return
        try:
            data = json.loads(self.resources_path.read_text(encoding="utf-8"))
            self._resources = data.get("resources", [])
            logger.info("课程资源加载完成：%d 个", len(self._resources))
        except Exception as exc:  # noqa: BLE001
            logger.error("课程资源加载失败: %s", exc)
            self._resources = []

    @property
    def resources(self) -> list[dict[str, Any]]:
        return self._resources

    def get_all(self) -> list[dict[str, Any]]:
        return list(self._resources)

    def get_by_chapter(self, chapter: str) -> list[dict[str, Any]]:
        if not chapter:
            return []
        return [r for r in self._resources if r.get("chapter") and chapter in r["chapter"]]

    def get_by_type(self, rtype: str) -> list[dict[str, Any]]:
        return [r for r in self._resources if r.get("type") == rtype]

    def extract_chapter(self, question: str) -> str:
        """从问题中提取章节名"""
        q = question or ""
        # 先匹配"第X章"
        m = re.search(r"第([一二三四五六七八九十\d]+)章", q)
        if m:
            num_str = m.group(1)
            num = _CN_MAP.get(num_str)
            if num is None:
                try:
                    num = int(num_str)
                except ValueError:
                    num = None
            if num and 1 <= num <= 10:
                # 从资源中找对应章节
                for r in self._resources:
                    ch = r.get("chapter", "")
                    if ch.startswith(f"第{_CN_NUMS[num]}章"):
                        return ch
        # 再匹配关键词
        for pattern, chapter in _CHAPTER_PATTERNS[1:]:  # 跳过第一个（已处理）
            if re.search(pattern, q):
                return chapter
        return ""

    def extract_types(self, question: str) -> list[str]:
        """从问题中提取资源类型列表"""
        q = question or ""
        types: list[str] = []
        for rtype, kws in _TYPE_KEYWORDS.items():
            if any(kw in q for kw in kws):
                types.append(rtype)
        return types

    def match(self, question: str) -> list[dict[str, Any]]:
        """根据问题匹配资源

        匹配优先级：
        1. 章节 + 类型 → 该章节该类型
        2. 仅章节 → 该章节所有资源（PPT+视频）
        3. 仅类型 → 该类型所有资源
        4. 都没有 → 全局资源（教材+题库+大纲）
        """
        chapter = self.extract_chapter(question)
        types = self.extract_types(question)

        if chapter and types:
            results = [r for r in self._resources
                       if r.get("chapter") and chapter in r["chapter"] and r.get("type") in types]
            if results:
                return results
        if chapter:
            results = [r for r in self._resources
                       if r.get("chapter") and chapter in r["chapter"]]
            if results:
                return results
        if types:
            results = [r for r in self._resources if r.get("type") in types]
            if results:
                return results
        # 兜底：全局资源
        return [r for r in self._resources if not r.get("chapter")]

    def format_answer(self, question: str, matched: list[dict[str, Any]]) -> str:
        """格式化资源回答（不依赖 LLM，直接输出资源列表）"""
        if not matched:
            return "抱歉，未找到对应的课程资料。你可以说明需要哪一章的课件、视频或教材，我来帮你找。"

        chapter = self.extract_chapter(question)
        lines: list[str] = []
        if chapter:
            lines.append(f"为你找到{chapter}的相关资料：")
        else:
            lines.append("为你找到以下课程资料：")
        lines.append("")

        type_labels = {"ppt": "课件", "video": "视频", "pdf": "教材",
                       "exercise": "题库", "syllabus": "教学大纲", "doc": "文档"}
        for i, r in enumerate(matched, 1):
            label = type_labels.get(r.get("type", ""), r.get("type", ""))
            lines.append(f"{i}. 【{label}】{r['title']}")
            if r.get("description"):
                lines.append(f"   {r['description']}")
        lines.append("")
        lines.append("点击下方链接即可下载或播放。")
        return "\n".join(lines)
