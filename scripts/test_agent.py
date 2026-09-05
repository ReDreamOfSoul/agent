# -*- coding: utf-8 -*-
"""命令行联调脚本

用法：
    python scripts/test_agent.py "什么是齐次变换矩阵？"     # 单次问答
    python scripts/test_agent.py --chat                     # 交互式问答
    python scripts/test_agent.py --homework "题目" "我的答案"  # 作业批阅测试

说明：
- 未配置 LLM 服务时使用 dummy 后端（本地模拟应答），保证全链路可运行；
- 需先运行 scripts/build_kb.py 构建知识库索引。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_config  # noqa: E402
from app.core.llm import create_llm  # noqa: E402
from app.core.session import SessionManager  # noqa: E402
from app.core.study_store import StudyStore  # noqa: E402
from app.core.workflow import WorkflowEngine  # noqa: E402
from app.rag.knowledge_base import KnowledgeBase  # noqa: E402


def build_engine(cfg, llm):
    kb_cfg = cfg.get("knowledge_base", {}) or {}
    ret_cfg = cfg.get("retrieval", {}) or {}
    kb = KnowledgeBase(kb_cfg.get("source_dir", "data/knowledge_base"),
                       kb_cfg.get("index_dir", "data/index"),
                       chunk_size=kb_cfg.get("chunk_size", 500),
                       chunk_overlap=kb_cfg.get("chunk_overlap", 60))
    if not kb.load():
        print("[错误] 知识库索引不存在，请先运行: python scripts/build_kb.py")
        sys.exit(1)
    study = StudyStore(cfg.get("study_db", {}).get("path", "data/study.db"))
    sessions = SessionManager(cfg.get("server", {}).get("session_timeout_minutes", 30))
    workflow = WorkflowEngine(kb, llm, sessions, study,
                              top_k=ret_cfg.get("top_k", 5),
                              threshold=ret_cfg.get("threshold", 0.72),
                              threshold_keyword=ret_cfg.get("threshold_keyword"))
    return workflow


def print_result(r) -> None:
    print("\n" + "=" * 60)
    print(f"问题        : {r.question}")
    print(f"意图        : {r.intent}")
    print(f"知识库命中  : {'是' if r.hit else '否'}（得分 {r.best_score}，模式 {r.retrieval_mode}）")
    print(f"生成方式    : {'基于知识库' if r.generation_mode == 'kb' else '通用兜底'}")
    print("-" * 60)
    print(r.answer)
    if r.followup_question:
        print("-" * 60)
        print(f"[引导式追问] {r.followup_question}")
    if r.sources:
        print("-" * 60)
        print("参考来源：")
        for s in r.sources[:3]:
            print(f"  · {s['source']}（{s['chapter'] or '未标注章节'}，第{s['page']}页）得分 {s['score']}")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="课程智能体命令行联调")
    parser.add_argument("question", nargs="?", default=None, help="单次问答的问题")
    parser.add_argument("--chat", action="store_true", help="交互式问答模式")
    parser.add_argument("--homework", nargs=2, metavar=("题目", "答案"), help="作业批阅测试")
    args = parser.parse_args()

    cfg = load_config()
    llm = create_llm(cfg)
    workflow = build_engine(cfg, llm)

    if args.homework:
        q, a = args.homework
        result = workflow.homework("cli", q, a)
        print("\n=== 作业批阅结果 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.question:
        r = workflow.run(workflow.sessions.create(), args.question)
        print_result(r)
        return 0

    if args.chat:
        print("进入交互式问答（输入 exit 退出）")
        session_id = workflow.sessions.create()
        workflow.study.touch_session(session_id)
        while True:
            try:
                q = input("\n学生提问 > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("exit", "quit", "退出"):
                break
            if not q:
                continue
            r = workflow.run(session_id, q)
            print_result(r)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
