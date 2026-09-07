# -*- coding: utf-8 -*-
"""工作流核心单元测试（无需真实知识库与模型服务）

运行：python -m pytest tests/ -v  或  python tests/test_workflow.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.homework import judge as homework_judge  # noqa: E402
from app.core.llm import DummyLLM  # noqa: E402
from app.core.session import SessionManager  # noqa: E402
from app.core.study_store import StudyStore  # noqa: E402
from app.core.workflow import IntentClassifier, WorkflowEngine  # noqa: E402
from app.rag.embedder import NoneEmbedder  # noqa: E402
from app.rag.knowledge_base import KnowledgeBase  # noqa: E402
from app.rag.parsers.pdf_parser import TextChunk  # noqa: E402

SAMPLE_CORPUS = [
    TextChunk(text="机器人运动学分为正运动学与逆运动学两类问题。正运动学已知关节变量求解末端位姿，"
                   "逆运动学已知末端位姿反解关节变量。齐次变换矩阵用于描述连杆坐标系之间的位姿关系。",
              source="sample.pdf", chapter="第三章 机器人运动学", page=3, resource_type="教材/文献"),
    TextChunk(text="机器视觉中常用滤波去噪算法包括中值滤波、高斯滤波和均值滤波。"
                   "中值滤波对椒盐噪声抑制效果较好，高斯滤波适用于高斯噪声。",
              source="sample.pdf", chapter="第六章 机器人感知", page=6, resource_type="教材/文献"),
    TextChunk(text="机器人轨迹规划分为关节空间轨迹规划与笛卡尔空间轨迹规划两类。"
                   "常用插补方法包括直线插补、圆弧插补与多项式插补。",
              source="sample.pdf", chapter="第五章 机器人轨迹规划", page=5, resource_type="教材/文献"),
    TextChunk(text="题目：什么是齐次变换矩阵？\n参考答案：齐次变换矩阵是用于描述两个坐标系之间平移与旋转关系的4×4矩阵。",
              source="题库.doc", chapter="", page=0, resource_type="题库"),
]


def make_engine():
    tmp = tempfile.mkdtemp()
    kb = KnowledgeBase(tempfile.mkdtemp(), tempfile.mkdtemp(), embedder=NoneEmbedder())
    kb.chunks = list(SAMPLE_CORPUS)
    kb.bm25.build([c.text for c in kb.chunks])
    study = StudyStore(Path(tmp) / "study.db")
    sessions = SessionManager()
    llm = DummyLLM()
    workflow = WorkflowEngine(kb, llm, sessions, study, top_k=5, threshold=0.72,
                              threshold_keyword=0.5)
    return workflow, study


def test_intent_classifier():
    clf = IntentClassifier()
    assert clf.classify("帮我提交作业") == "homework"
    assert clf.classify("这道习题怎么做") == "exercise"
    assert clf.classify("想了解机器人学的背景") == "background"
    assert clf.classify("什么是齐次变换矩阵") == "knowledge"


def test_kb_retrieval_hit():
    workflow, _ = make_engine()
    ret = workflow.kb.retrieve("机器人运动学分为哪两类问题", top_k=5, threshold=0.72,
                               threshold_keyword=0.5)
    assert ret["hit"] is True
    assert ret["results"], "应检索到结果"
    assert any("运动学" in r["chunk"].text for r in ret["results"])


def test_workflow_kb_generation():
    workflow, _ = make_engine()
    sid = workflow.sessions.create()
    r = workflow.run(sid, "请介绍一种机器视觉滤波去噪算法")
    assert r.hit is True
    assert r.generation_mode == "kb"
    assert "中值滤波" in r.answer or "高斯滤波" in r.answer or "均值滤波" in r.answer
    assert r.answer.strip()
    assert r.trace, "应包含流程轨迹"


def test_workflow_generic_fallback():
    workflow, _ = make_engine()
    sid = workflow.sessions.create()
    r = workflow.run(sid, "量子计算与机器人有什么关系")
    assert r.hit is False
    assert r.generation_mode == "generic"
    assert r.answer.strip()


def test_workflow_record_and_followup():
    workflow, study = make_engine()
    sid = workflow.sessions.create()
    workflow.run(sid, "什么是齐次变换矩阵")
    rows = study._conn.execute("SELECT COUNT(*) c FROM interactions").fetchone()["c"]
    assert rows == 1


def test_homework_exact():
    workflow, _ = make_engine()
    result = workflow.homework("t", "什么是齐次变换矩阵",
                               "齐次变换矩阵是用于描述两个坐标系之间平移与旋转关系的4×4矩阵")
    assert result["score"] == 1.0
    assert result["mode"] == "exact"


def test_homework_keyword_partial():
    result = homework_judge("齐次变换矩阵用于描述坐标系的变换关系",
                            "什么是齐次变换矩阵", kb=None, llm=None)
    assert 0.0 < result["score"] < 1.0 or result["mode"] in ("keyword", "llm")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n共 {len(tests)} 项测试全部通过")
