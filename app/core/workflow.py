# -*- coding: utf-8 -*-
"""流程编排引擎（工作流状态机）

对应设计文档第 4 章「工作流编排设计」主流程：
开始 → 意图识别 → 知识库检索 → 判断RAG输出（命中→LLM整合生成 / 未命中→通用逻辑生成）
→ 文本处理 → 回复 → 交互记录

每次调用返回完整节点轨迹（trace），支撑学情记录与系统调试（流程透明、可审计）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..rag.knowledge_base import KnowledgeBase
from .course_meta import CourseMeta
from .homework import judge as homework_judge
from .resource_manager import ResourceManager
from .llm import BaseLLM, DummyLLM, LLMError
from .session import SessionManager, Turn
from .study_store import StudyStore
from .text_processor import process_answer

logger = logging.getLogger("agent.workflow")

# 意图关键词（规则式意图识别，可按课程实际扩充）
_INTENT_KEYWORDS = {
    "course_info": ["课程", "教材", "教科书", "参考书", "任课老师", "授课老师", "谁教", "任课教师",
                    "学分", "学时", "考核", "成绩评定", "考试方式", "先修课程", "前置课程",
                    "教学大纲", "教学内容", "课程目标", "培养目标", "课程介绍", "课程简介",
                    "这门课", "本课程", "用什么书", "上课老师", "课程性质", "适用专业"],
    "resource": ["资料", "课件", "PPT", "ppt", "幻灯片", "下载", "给我一份", "有没有",
                 "课本", "题库", "学习材料", "讲课", "录像", "学习资料", "发我", "传我"],
    "homework": ["提交作业", "交作业", "作业批改", "批改作业", "帮我批改", "批阅作业", "我的作业"],
    "exercise": ["习题", "例题", "怎么解", "怎么做", "求解", "计算题", "题目", "解答一下", "分析题"],
    "background": ["背景", "拓展", "补充资料", "文献", "前沿", "了解更多", "更多内容", "相关资源"],
}


@dataclass
class NodeTrace:
    """单个节点的执行轨迹"""
    node: str
    status: str = "ok"           # ok | fallback | error
    detail: dict = field(default_factory=dict)


@dataclass
class WorkflowResult:
    session_id: str
    question: str
    intent: str
    hit: bool
    best_score: float
    retrieval_mode: str
    generation_mode: str          # kb | generic
    answer: str
    raw_answer: str
    sources: list[dict]
    followup_question: str
    keywords: list[str]
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "question": self.question,
            "intent": self.intent,
            "knowledge_base_hit": self.hit,
            "best_score": self.best_score,
            "retrieval_mode": self.retrieval_mode,
            "generation_mode": self.generation_mode,
            "answer": self.answer,
            "sources": self.sources,
            "followup_question": self.followup_question,
            "trace": self.trace,
        }


class IntentClassifier:
    """规则式意图识别：课程信息 > 资源请求 > 作业 > 习题 > 背景 > 知识点"""

    # 资源请求动词：含这些词时优先判 resource（避免被 course_info 截走）
    _RESOURCE_VERBS = ["下载", "给我", "有没有", "发我", "传我", "学习材料", "学习资料",
                        "给我一份", "给我个", "给我一个", "帮我找", "帮我下"]

    def classify(self, question: str) -> str:
        q = (question or "").strip()
        if any(v in q for v in self._RESOURCE_VERBS):
            return "resource"
        for intent, kws in _INTENT_KEYWORDS.items():
            if any(kw in q for kw in kws):
                return intent
        return "knowledge"


class WorkflowEngine:
    def __init__(self, kb: KnowledgeBase, llm: BaseLLM,
                 sessions: SessionManager, study: StudyStore,
                 top_k: int = 5, threshold: float = 0.72,
                 threshold_keyword: float | None = None,
                 vector_weight: float = 0.6, keyword_weight: float = 0.4):
        self.kb = kb
        self.llm = llm
        self.sessions = sessions
        self.study = study
        self.classifier = IntentClassifier()
        self.course_meta = CourseMeta()
        self.resource_manager = ResourceManager()
        self.top_k = top_k
        self.threshold = threshold
        self.threshold_keyword = threshold_keyword
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight

    # ---------- 主流程 ----------
    def run(self, session_id: str, question: str) -> WorkflowResult:
        trace: list[dict] = []
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")

        # 节点1：开始
        trace.append({"node": "开始", "detail": {"input": question[:50]}})

        # 节点2：意图识别
        intent = self.classifier.classify(question)
        trace.append({"node": "意图识别", "detail": {"intent": intent}})

        # 节点2.5：多轮上下文与追问状态机
        # - 取最近几轮对话供 LLM 解析指代；
        # - 若上一轮生成了引导式追问，则本轮输入视为对追问的回答，将追问并入历史。
        pending_followup = self.sessions.pop_pending_followup(session_id)
        history = self.sessions.recent_messages(session_id, max_rounds=3)
        if pending_followup:
            history.append({"role": "assistant", "content": pending_followup})
        trace.append({"node": "对话上下文", "detail": {
            "history_rounds": len(history) // 2,
            "replying_followup": bool(pending_followup)}})

        # 节点2.8：课程元信息分支（与知识点检索隔离，避免污染排序）
        if intent == "course_info":
            trace.append({"node": "课程元信息", "detail": {"meta_fields": len(self.course_meta.data)}})
            raw = self.course_meta.answer(question, self.llm, history=history)
            answer = process_answer(raw, keywords=[])
            sources = [{
                "source": "课程教学大纲", "chapter": "", "page": 0,
                "score": 1.0, "text": "课程元信息清单（课程名称/教师/教材/学分/考核/大纲等）",
                "video_url": "",
            }]
            trace.append({"node": "LLM生成", "detail": {"mode": "course_meta"}})
            trace.append({"node": "文本处理", "detail": {"length": len(answer)}})
            turn = Turn(question=question, answer=answer, intent=intent,
                        hit_knowledge_base=False, source_chunks=sources)
            self.sessions.append(session_id, turn)
            try:
                self.study.record_interaction(
                    session_id, intent=intent, question=question, answer=answer,
                    hit=False, best_score=0.0,
                    generation_mode="course_meta", retrieval_mode="course_meta",
                    followup_question="",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("交互记录写入失败: %s", exc)
            trace.append({"node": "回复", "detail": {"delivered": True}})
            return WorkflowResult(
                session_id=session_id, question=question, intent=intent,
                hit=False, best_score=0.0,
                retrieval_mode="course_meta", generation_mode="course_meta",
                answer=answer, raw_answer=raw, sources=sources,
                followup_question="", keywords=[], trace=trace,
            )

        # 节点2.9：课程资源请求分支（返回资料下载链接，不检索知识库）
        if intent == "resource":
            matched = self.resource_manager.match(question)
            trace.append({"node": "资源匹配", "detail": {"matched": len(matched)}})
            answer = self.resource_manager.format_answer(question, matched)
            sources = [{
                "source": r["title"], "chapter": r.get("chapter", ""), "page": 0,
                "score": 1.0, "text": r.get("description", ""),
                "video_url": r["url"] if r.get("type") == "video" else "",
                "resource_url": r.get("url", ""), "resource_type": r.get("type", ""),
            } for r in matched]
            trace.append({"node": "回复", "detail": {"delivered": True}})
            turn = Turn(question=question, answer=answer, intent=intent,
                        hit_knowledge_base=False, source_chunks=sources)
            self.sessions.append(session_id, turn)
            try:
                self.study.record_interaction(
                    session_id, intent=intent, question=question, answer=answer,
                    hit=False, best_score=0.0,
                    generation_mode="resource", retrieval_mode="resource",
                    followup_question="",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("交互记录写入失败: %s", exc)
            return WorkflowResult(
                session_id=session_id, question=question, intent=intent,
                hit=False, best_score=0.0,
                retrieval_mode="resource", generation_mode="resource",
                answer=answer, raw_answer=answer, sources=sources,
                followup_question="", keywords=[], trace=trace,
            )

        # 节点3：知识库检索
        retrieval = self.kb.retrieve(
            question, top_k=self.top_k, threshold=self.threshold,
            threshold_keyword=self.threshold_keyword,
            vector_weight=self.vector_weight, keyword_weight=self.keyword_weight,
        )
        trace.append({"node": "知识库检索", "detail": {
            "mode": retrieval["retrieval_mode"], "candidates": len(retrieval["results"]),
            "best_score": retrieval["best_score"],
        }})

        # 节点4：判断RAG输出
        hit = retrieval["hit"]
        trace.append({"node": "判断RAG输出", "status": "ok" if hit else "fallback",
                      "detail": {"hit": hit, "threshold": self.threshold}})

        # 节点5：LLM生成（整合生成 / 通用生成）
        # 每段截断至 480 字再送模型（块长 500 字，保留全文主体；避免关键句被截断导致模型误判"片段无原文"）
        if hit:
            contexts = [r["chunk"].text[:480] for r in retrieval["results"]]
            raw = self.llm.generate_with_kb(question, contexts, history=history)
            generation_mode = "kb"
        else:
            raw = self.llm.generate_generic(question, history=history)
            generation_mode = "generic"
        trace.append({"node": "LLM生成", "detail": {"mode": generation_mode}})

        # 节点6：文本处理
        keywords = [r["chunk"].chapter for r in retrieval["results"] if r["chunk"].chapter]
        answer = process_answer(raw, keywords=keywords[:6])
        trace.append({"node": "文本处理", "detail": {"length": len(answer)}})

        # 节点7：引导式追问（仅知识库命中的知识点类问题）
        followup = ""
        if hit and intent == "knowledge":
            try:
                followup = self.llm.generate_followup(question, answer[:300], keywords)
                # 追问状态机：记录待回答追问，下一轮输入将作为对追问的回答处理
                self.sessions.set_pending_followup(session_id, followup)
                trace.append({"node": "引导式追问", "detail": {"generated": True}})
            except LLMError:
                logger.warning("引导式追问生成失败，跳过")
                trace.append({"node": "引导式追问", "status": "fallback", "detail": {"generated": False}})

        # 节点8：回复 + 交互记录
        sources = [
            {"source": r["chunk"].source, "chapter": r["chunk"].chapter,
             "page": r["chunk"].page, "score": r["score"], "text": r["chunk"].text[:120],
             "video_url": r["chunk"].video_url or ""}
            for r in retrieval["results"][:5]
        ]
        turn = Turn(question=question, answer=answer, intent=intent,
                    hit_knowledge_base=hit, source_chunks=sources)
        self.sessions.append(session_id, turn)
        try:
            self.study.record_interaction(
                session_id, intent=intent, question=question, answer=answer,
                hit=hit, best_score=retrieval["best_score"],
                generation_mode=generation_mode, retrieval_mode=retrieval["retrieval_mode"],
                followup_question=followup,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("交互记录写入失败（本地缓冲重试由外层处理）: %s", exc)
            trace.append({"node": "交互记录", "status": "error", "detail": {"error": str(exc)}})
        trace.append({"node": "回复", "detail": {"delivered": True}})

        return WorkflowResult(
            session_id=session_id, question=question, intent=intent,
            hit=hit, best_score=retrieval["best_score"],
            retrieval_mode=retrieval["retrieval_mode"], generation_mode=generation_mode,
            answer=answer, raw_answer=raw, sources=sources,
            followup_question=followup, keywords=keywords, trace=trace,
        )

    # ---------- 作业批阅流程（作业提交—自动批阅—反馈推送） ----------
    def homework(self, session_id: str, question_text: str, student_answer: str) -> dict:
        result = homework_judge(student_answer, question_text, kb=self.kb, llm=self.llm)
        try:
            self.study.record_homework(
                session_id, question_text=question_text, student_answer=student_answer,
                score=result["score"], feedback=result["feedback"],
                matched_source=result["matched_source"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("作业记录写入失败: %s", exc)
        return result
