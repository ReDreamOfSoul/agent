# -*- coding: utf-8 -*-
"""作业批阅模块

对应设计文档「作业提交—自动批阅—反馈推送」：
- 在知识库题库中匹配对应题目；
- 客观题按标准答案归一化比对精确判定；
- 主观题按关键词/语义要点覆盖率评分；
- 生成批阅反馈并写入学情库。
"""
from __future__ import annotations

import logging
import re

from .text_processor import normalize_whitespace

logger = logging.getLogger("agent.homework")

ANSWER_MARKERS = [
    "参考答案", "标准答案", "答案：", "答案:", "答：", "答:", "【答案】", "[答案]",
    "正确答案", "解析：", "解析:", "【解析】",
]


def extract_answer_from_text(text: str) -> str | None:
    """从题库文本块中提取答案（出现在答案标记之后的部分）"""
    text = normalize_whitespace(text)
    for marker in ANSWER_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            seg = text[idx + len(marker):].strip()
            # 截取到下一个明显题目分隔（数字题号或空行）
            seg = re.split(r"\n\s*\n|(?:\n|^)\s*[0-9一二三四五六七八九十]+[、．.]\s*", seg)[0]
            if seg:
                return seg.strip()
    return None


def _normalize(text: str) -> str:
    """答案归一化：去空白、去标点、小写"""
    text = normalize_whitespace(text or "")
    text = re.sub(r"[\s，。；、,.；!?！？:：\"'“”‘’（）()【】\[\]\-—–]+", "", text)
    return text.lower()


def _locate_question(chunk_text: str, question_text: str) -> int:
    """在题库块中定位题目原文位置（优先完整句，其次关键短语）"""
    q = normalize_whitespace(question_text or "")
    text = normalize_whitespace(chunk_text)
    pos = text.find(q)
    if pos >= 0:
        return pos
    # 题目可能被分块截断：用最长 20 字前缀尝试
    for cut in (18, 15, 12):
        if len(q) > cut:
            pos = text.find(q[:cut])
            if pos >= 0:
                return pos
    return -1


def _extract_answer_after(chunk_text: str, start_pos: int) -> str | None:
    """从题目出现位置之后提取答案（首个答案标记之后、下一个题号之前）"""
    seg = chunk_text[start_pos:]
    for marker in ANSWER_MARKERS:
        idx = seg.find(marker)
        if idx >= 0:
            ans = seg[idx + len(marker):].strip()
            ans = re.split(r"\n\s*\n|(?:\n|^)\s*[0-9一二三四五六七八九十]+[、．.]\s*", ans)[0]
            if ans.strip():
                return ans.strip()
    return None


def keyword_overlap(student: str, reference: str) -> float:
    """主观题要点评分：基于关键词覆盖的均衡分（F1变体，兼顾完整性与精炼度）

    score = 0.5 * 召回率（参考要点被覆盖比例）+ 0.5 * 精确率（学生要点命中比例），
    避免冗长参考答案把精炼但正确的作答压分。
    """
    import jieba
    stop = {"", "的", "了", "是", "在", "与", "和", "或", "等", "一个", "请", "答案", "解析",
            "通过", "进行", "认为", "情况", "达到", "从而", "其"}
    ref_terms = {t for t in jieba.cut(reference) if t not in stop and len(t) > 1}
    stu_terms = {t for t in jieba.cut(student) if t not in stop and len(t) > 1}
    if not ref_terms:
        return 0.0
    if not stu_terms:
        return 0.0
    hit = len(ref_terms & stu_terms)
    recall = hit / len(ref_terms)
    precision = hit / len(stu_terms)
    return round(0.5 * recall + 0.5 * precision, 2)


def judge(student_answer: str, question_text: str, kb=None, llm=None) -> dict:
    """执行作业批阅

    Args:
        student_answer: 学生作答文本
        question_text: 题目文本
        kb: 知识库（用于匹配题库题目与参考答案），可为 None
        llm: LLM 客户端（可选，用于主观题反馈润色），可为 None

    Returns:
        {"score": 0~1, "feedback": str, "matched_source": str,
         "matched_question": str, "reference": str|None, "mode": "exact"|"keyword"|"llm"}
    """
    question_text = normalize_whitespace(question_text or "")
    student = normalize_whitespace(student_answer or "")
    if not student:
        return {"score": 0.0, "feedback": "未检测到作答内容，请重新提交。",
                "matched_source": "", "matched_question": question_text, "reference": None, "mode": "none"}

    reference, matched_source, matched_question = None, "", question_text
    if kb is not None:
        try:
            ret = kb.retrieve(question_text, top_k=3, threshold=0.0)
            for item in ret["results"]:
                chunk_text = item["chunk"].text
                # 优先定位题目原文所在位置，取其后的答案（避免题库多题共存时错配）
                pos = _locate_question(chunk_text, question_text)
                ref = (_extract_answer_after(chunk_text, pos) if pos >= 0
                       else extract_answer_from_text(chunk_text))
                # 过滤占位符/过短答案（题库中部分题目答案以"/"占位，无法用于比对）
                if ref and len(_normalize(ref)) >= 3:
                    reference, matched_source = ref, item["chunk"].source
                    matched_question = chunk_text[:120]
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("题库匹配失败: %s", exc)

    # 1) 客观题精确比对
    if reference:
        if _normalize(reference) == _normalize(student):
            return {"score": 1.0,
                    "feedback": "批阅结果：回答正确。建议结合解析进一步理解解题思路。",
                    "matched_source": matched_source, "matched_question": matched_question,
                    "reference": reference, "mode": "exact"}

        # 2) 关键词要点匹配
        ratio = keyword_overlap(student, reference)
        if ratio >= 0.6:
            score = ratio
            feedback = (f"批阅结果：要点覆盖较完整（相似度 {ratio:.0%}），"
                        "可对照参考答案补充细节表述。")
        elif ratio > 0.0:
            score = ratio
            feedback = (f"批阅结果：部分要点正确（相似度 {ratio:.0%}）。"
                        "建议对照以下参考答案完善：\n" + reference[:200])
        else:
            score = 0.0
            feedback = "批阅结果：与参考答案要点不匹配，请复习相关知识点后重做。"
        return {"score": round(score, 2), "feedback": feedback, "matched_source": matched_source,
                "matched_question": matched_question, "reference": reference, "mode": "keyword"}

    # 3) 无参考答案：按作答完整度给出保守评估（交由教师复核）；LLM 可用时生成诊断性反馈
    length = len(student)
    score = min(0.5, length / 200.0) if length > 0 else 0.0
    feedback = "未在题库中找到该题参考答案，已按作答完整度给出保守评分，请任课教师复核。"
    if llm is not None:
        try:
            feedback = llm.subjective_feedback(question_text, student, score)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 主观题反馈生成失败，回退保守反馈: %s", exc)
    return {"score": round(score, 2),
            "feedback": feedback,
            "matched_source": matched_source, "matched_question": matched_question,
            "reference": None, "mode": "llm"}
