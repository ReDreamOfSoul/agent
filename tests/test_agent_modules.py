# -*- coding: utf-8 -*-
"""新增模块单元测试：习题抽题解析 / 知识图谱 / 学情画像与导出

运行：python tests/test_agent_modules.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.exercise import _bank_questions, extract_questions  # noqa: E402
from app.core.graph import graph_overview, graph_query  # noqa: E402
from app.core.study_store import StudyStore  # noqa: E402
from app.rag.embedder import NoneEmbedder  # noqa: E402
from app.rag.knowledge_base import KnowledgeBase  # noqa: E402
from app.rag.parsers.pdf_parser import TextChunk  # noqa: E402

# ---------- 习题抽题解析 ----------

REAL_BANK = (
    "1、机器人作业过程分两类，一类是非接触式，一类是接触式。下面哪种机器人属于非接触式作业机器人。\r"
    "\rA、  拧螺钉机器人 \rB、 装配机器人 \rC、 抛光机器人 \rD、 弧焊机器人 \r答案： D \r"
    "2、何为D-H参数法，如何用其描述一个6转动关节的机器人？\r\r答案： \r"
    "D-H参数法主要指使用连杆参数来描述机构运动关系;可以用6组参数（ai,αi，di）来描述其18个固定参数。\r"
    "3、尝试用拉格朗日法建立机器人动力学方程。\r\r答案： \r/\r/\r/"
)


def test_exercise_extract_real_format():
    """真实题库格式（\\r 换行、客观题选项、占位答案过滤）"""
    qs = extract_questions(REAL_BANK)
    assert len(qs) == 2, f"应解析出 2 道可用题（占位答案题被过滤），实际 {len(qs)}"
    assert qs[0]["question"].startswith("机器人作业过程分两类")
    assert qs[0]["answer"].strip() == "D"          # 客观题答案
    assert "D-H参数法" in qs[1]["question"]


def test_exercise_extract_cross_chunk():
    """同一文件分块：题目与答案被截断到不同块时，合并后仍能解析"""
    block1 = TextChunk(text="1、题目甲的核心内容是什么？\r答案： 答案甲完整表述\r\r2、题目乙简述……",
                       source="题库.doc", chapter="", page=0, resource_type="题库")
    block2 = TextChunk(text="\r答案： 答案乙完整表述\r\r3、题目丙？\r答案： / /",
                       source="题库.doc", chapter="", page=0, resource_type="题库")
    kb = KnowledgeBase(tempfile.mkdtemp(), tempfile.mkdtemp(), embedder=NoneEmbedder())
    kb.chunks = [block1, block2]
    pool = _bank_questions(kb, chapter="")
    assert len(pool) == 2
    assert "题目甲" in pool[0]["question"] and "答案甲" in pool[0]["answer"]
    assert "题目乙" in pool[1]["question"] and "答案乙" in pool[1]["answer"]


def test_exercise_extract_compat_format():
    """兼容「题目：…参考答案：…」格式"""
    qs = extract_questions("题目：什么是齐次变换矩阵？\n参考答案：4×4矩阵。\n题目：什么是逆运动学？\n参考答案：末端位姿反解关节变量。")
    assert len(qs) == 2
    assert "齐次变换矩阵" in qs[0]["question"]


# ---------- 知识图谱 ----------

def test_graph_overview():
    chapters = graph_overview()
    assert len(chapters) == 10
    assert chapters[0]["prerequisite"] is None
    assert chapters[0]["successor"] == "机器人本体结构"
    assert chapters[-1]["successor"] is None
    assert all(c["objective"] for c in chapters)


def test_graph_query():
    r = graph_query("机器人运动学", "prerequisite")
    assert r["matched_chapter"] == "机器人运动学"
    assert r["related"][0]["name"] == "机器人本体结构"
    r2 = graph_query("轨迹规划", "successor")
    assert r2["related"][0]["name"] == "机器人控制系统"
    r3 = graph_query("轨迹规划", "objective")
    assert r3["related"]
    r4 = graph_query("量子计算", "prerequisite")
    assert r4["matched_chapter"] is None and r4["related"] == []


# ---------- 学情画像与导出 ----------

def _study():
    return StudyStore(Path(tempfile.mkdtemp()) / "study.db")


def test_study_session_summary():
    st = _study()
    st.touch_session("s1")
    st.record_interaction("s1", intent="knowledge", question="什么是D-H参数法",
                          answer="答", hit=True, best_score=1.0,
                          generation_mode="kb", retrieval_mode="keyword")
    st.record_interaction("s1", intent="knowledge", question="量子计算",
                          answer="答", hit=False, best_score=0.2,
                          generation_mode="generic", retrieval_mode="keyword")
    st.record_homework("s1", question_text="题A", student_answer="答",
                       score=0.4, feedback="待订正", matched_source="题库.doc")
    s = st.get_session_summary("s1")
    assert s["total_questions"] == 2
    assert abs(s["hit_rate"] - 0.5) < 1e-6
    assert s["homework_count"] == 1
    assert s["weak_homework"][0]["score"] == 0.4
    assert len(s["missed_questions"]) == 1 and s["missed_questions"][0]["question"] == "量子计算"


def test_study_export_rows():
    st = _study()
    st.touch_session("s2")
    st.record_interaction("s2", intent="knowledge", question="题", answer="答",
                          hit=True, best_score=0.9, generation_mode="kb",
                          retrieval_mode="keyword")
    cols, rows = st.export_rows("interactions", since=0)
    assert "question" in cols and "hit" in cols
    assert len(rows) == 1
    try:
        st.export_rows("not_exist", since=0)
        raise AssertionError("应拒绝非法表名")
    except ValueError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n共 {len(tests)} 项测试全部通过")
