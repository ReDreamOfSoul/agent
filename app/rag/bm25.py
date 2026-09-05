# -*- coding: utf-8 -*-
"""BM25（Okapi）关键词检索索引

配合向量检索构成混合检索（对应设计文档「混合检索：向量+BM25」）。
使用 jieba 中文分词，并注入机器人学课程术语提升专业分词质量。
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import re
from pathlib import Path
from typing import Sequence

import jieba

logger = logging.getLogger("agent.retriever.bm25")

# 课程术语词典（分词增强，可按课程内容扩充）
COURSE_TERMS = [
    "机器人学", "机器人运动学", "运动学", "机器人动力学", "动力学", "机器人轨迹规划", "轨迹规划",
    "机器人控制系统", "控制系统", "齐次变换矩阵", "齐次变换", "旋转矩阵", "姿态描述", "关节变量",
    "工作空间", "逆运动学", "正运动学", "雅可比矩阵", "奇异位形", "轨迹插补", "插补", "PID控制",
    "力位混合控制", "机器视觉", "滤波去噪", "传感器", "仿生机器人", "工业机器人", "操纵型机器人",
    "机器人的编程与语言", "示教编程", "本体结构", "连杆坐标系", "D-H参数", "末端位姿", "末端执行器",
    "运动学方程", "动力学方程", "拉格朗日方程", "牛顿-欧拉", "路径规划", "避障", "视觉伺服",
    "抓取规划", "移动机器人", "足式机器人", "空间机器人", "机器人学导论", "教学大纲", "题库", "绪论",
]
for _term in COURSE_TERMS:
    jieba.add_word(_term)


# 停用词：通用虚词 + 提问功能词（不承载领域信息，分词与覆盖率计算均剔除）
# 注意：习题类高频词（特点/两类/几种/主要/简述等）在每章习题中大量出现，
# 保留它们会让"习题块"在覆盖率和排序中虚高，挤掉真正的正文答案块。
STOP_WORDS = {
    " ", "", "的", "了", "是", "在", "与", "和", "或", "等", "一个", "我们", "请", "介绍",
    "什么", "一下", "分为", "哪", "哪些", "如何", "怎么", "为什么", "有没有", "有", "关系",
    "中", "吗", "呢", "一种", "两种", "几类", "包括", "其中", "以及", "及其", "可以", "能够",
    "属于", "进行", "相关", "请问",
    # 习题/提问功能词（高频、无领域区分度）
    "特点", "两类", "几种", "各", "各有", "主要", "简述", "说明", "区别", "不同", "意义", "作用",
    "比较", "方面", "部分", "内容", "分别", "常见", "常用", "典型", "基本", "重要",
}


# 分词 token 有效性：必须含至少一个中文字符/字母/数字（过滤标点、"？"等符号 token）
_TOKEN_KEEP = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def tokenize(text: str) -> list[str]:
    """中文分词，过滤停用词、标点符号 token 与纯数字"""
    tokens = [w.strip().lower() for w in jieba.cut(text)]
    return [t for t in tokens if t not in STOP_WORDS and _TOKEN_KEEP.search(t) and not t.isdigit()]


class BM25Index:
    """Okapi BM25 索引（k1=1.5, b=0.75）"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_count = 0
        self.avgdl = 0.0
        self.doc_len: list[int] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_freq: dict[str, int] = {}
        self.idf: dict[str, float] = {}

    def build(self, docs: Sequence[str]) -> None:
        self.doc_tokens = [tokenize(d) for d in docs]
        self.doc_count = len(self.doc_tokens)
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = sum(self.doc_len) / self.doc_count if self.doc_count else 0.0
        self.doc_freq = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        self.idf = {
            term: math.log(1 + (self.doc_count - df + 0.5) / (df + 0.5))
            for term, df in self.doc_freq.items()
        }
        logger.info("BM25 索引构建完成: %d 篇文档, %d 个词项", self.doc_count, len(self.idf))

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        if not self.doc_tokens:
            return 0.0
        dl = self.doc_len[doc_idx]
        tokens = self.doc_tokens[doc_idx]
        tf_map: dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        score = 0.0
        for term in set(query_tokens):
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            idf = self.idf.get(term, 0.0)
            denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else tf
            score += idf * (tf * (self.k1 + 1)) / denom
        return score

    def max_idf(self) -> float:
        """语料中最高的 idf 值（未收录词按下限权重使用）"""
        if not self.idf:
            return 1.0
        return max(self.idf.values())

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float, float]]:
        """返回 [(doc_idx, score, matched_weight)] 按得分降序

        matched_weight：该文档命中的查询词的 idf 加权和（未收录词按最高 idf 计权，
        既惩罚"关键术语在知识库缺失"，也防止常见词稀释覆盖率），供阈值判定使用。
        """
        q_tokens = tokenize(query)
        if not q_tokens or not self.doc_count:
            return []
        m_idf = self.max_idf()
        total_w = self.query_weight(q_tokens, m_idf)
        scored: list[tuple[int, float, float]] = []
        for i in range(self.doc_count):
            tf_map: dict[str, int] = {}
            for t in self.doc_tokens[i]:
                tf_map[t] = tf_map.get(t, 0) + 1
            matched = sum(1 for t in set(q_tokens) if t in tf_map)
            if matched == 0:
                continue
            mw = sum(self.idf.get(t, m_idf) for t in set(q_tokens) if t in tf_map)
            scored.append((i, self.score(q_tokens, i), mw))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def query_weight(self, q_tokens: list[str], max_idf: float | None = None) -> float:
        """查询词的 idf 加权权重和（未收录词按最高 idf 计权）"""
        m_idf = max_idf if max_idf is not None else self.max_idf()
        return sum(self.idf.get(t, m_idf) for t in set(q_tokens))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({
                "k1": self.k1, "b": self.b, "doc_count": self.doc_count,
                "avgdl": self.avgdl, "doc_len": self.doc_len,
                "doc_tokens": self.doc_tokens, "doc_freq": self.doc_freq,
                "idf": self.idf,
            }, f, protocol=4)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        with path.open("rb") as f:
            data = pickle.load(f)
        obj = cls(k1=data["k1"], b=data["b"])
        obj.doc_count = data["doc_count"]
        obj.avgdl = data["avgdl"]
        obj.doc_len = data["doc_len"]
        obj.doc_tokens = data["doc_tokens"]
        obj.doc_freq = data["doc_freq"]
        obj.idf = data["idf"]
        return obj


def dump_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
