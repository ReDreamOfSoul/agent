# -*- coding: utf-8 -*-
"""统一MCP协议：工具注册与实现

对应设计文档 7.1「统一MCP协议对接」：
以统一MCP协议注册 course_kb_retrieval / exercise_judge / study_record / graph_query 四个工具，
由流程编排层（或外部平台）按需调用。

工具 Schema 遵循 MCP tools/list 规范（JSON Schema 格式）。
"""
from __future__ import annotations

from typing import Any, Callable

from ..core.graph import graph_query

# ---------------- 工具 Schema 定义 ----------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "course_kb_retrieval",
        "description": "检索《机器人学导论》课程知识库（教材/课件/题库/教学大纲），返回与问题相关的课程片段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "学生问题文本"},
                "top_k": {"type": "integer", "description": "返回片段数量", "default": 5},
                "resource_type": {"type": "string", "description": "资源类型过滤（教材/课件/题库/教学大纲），可空"},
                "chapter": {"type": "string", "description": "章节过滤，可空"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "exercise_judge",
        "description": "习题/作业批阅：在题库中匹配题目参考答案，对客观题精确判定、主观题要点评分，返回批阅结果与反馈。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question_text": {"type": "string", "description": "题目文本"},
                "student_answer": {"type": "string", "description": "学生作答内容"},
            },
            "required": ["question_text", "student_answer"],
        },
    },
    {
        "name": "study_record",
        "description": "学情数据写入：记录一次交互/作业事件到学情数据库。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "event_type": {"type": "string", "enum": ["interaction", "homework", "feedback"]},
                "payload": {"type": "object", "description": "事件详情"},
            },
            "required": ["session_id", "event_type", "payload"],
        },
    },
    {
        "name": "graph_query",
        "description": "知识图谱/目标图谱节点查询：返回某知识点的前置/后继知识点与对应学习目标。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "知识点名称"},
                "relation_type": {"type": "string", "enum": ["prerequisite", "successor", "objective"], "default": "prerequisite"},
            },
            "required": ["node"],
        },
    },
]


class ToolRegistry:
    """MCP 工具注册表：name → (schema, handler)"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[dict, Callable]] = {}

    def register(self, name: str, schema: dict, handler: Callable) -> None:
        self._tools[name] = (schema, handler)

    def list(self) -> list[dict]:
        return [{"name": name, "description": schema["description"],
                 "inputSchema": schema["inputSchema"]} for name, (schema, _) in self._tools.items()]

    def call(self, name: str, arguments: dict) -> Any:
        entry = self._tools.get(name)
        if entry is None:
            raise KeyError(f"未知工具: {name}")
        return entry[1](arguments or {})


def build_registry(kb, workflow) -> ToolRegistry:
    """装配工具注册表（注入知识库与工作流引擎）"""
    reg = ToolRegistry()

    def _kb_retrieval(args: dict) -> dict:
        ret = kb.retrieve(args.get("query", ""), top_k=args.get("top_k", 5))
        results = []
        for r in ret["results"][: args.get("top_k", 5)]:
            c = r["chunk"]
            if args.get("resource_type") and args.get("resource_type") not in c.resource_type:
                continue
            if args.get("chapter") and args.get("chapter") not in (c.chapter or ""):
                continue
            results.append({"text": c.text, "source": c.source, "chapter": c.chapter,
                            "page": c.page, "score": r["score"]})
        return {"query": args.get("query"), "hit": ret["hit"], "best_score": ret["best_score"],
                "results": results[: args.get("top_k", 5)]}

    def _exercise_judge(args: dict) -> dict:
        return workflow.homework(args.get("session_id", "mcp"),
                                 args.get("question_text", ""), args.get("student_answer", ""))

    def _study_record(args: dict) -> dict:
        payload = args.get("payload", {})
        event_type = args.get("event_type", "interaction")
        if event_type == "interaction":
            workflow.study.record_interaction(
                args["session_id"], intent=payload.get("intent", "knowledge"),
                question=payload.get("question", ""), answer=payload.get("answer", ""),
                hit=payload.get("hit", False), best_score=payload.get("best_score", 0.0),
                generation_mode=payload.get("generation_mode", "generic"),
                retrieval_mode=payload.get("retrieval_mode", ""),
            )
        elif event_type == "homework":
            workflow.study.record_homework(
                args["session_id"], question_text=payload.get("question_text", ""),
                student_answer=payload.get("student_answer", ""), score=payload.get("score", 0.0),
                feedback=payload.get("feedback", ""), matched_source=payload.get("matched_source", ""),
            )
        elif event_type == "feedback":
            workflow.study.record_feedback(
                args["session_id"], question=payload.get("question", ""),
                rating=payload.get("rating", 3), comment=payload.get("comment", ""),
            )
        return {"status": "ok"}

    # 双图谱查询：章节级前置/后继关系与学习目标（完整知识点级图谱数据建设后可替换实现）
    def _graph_query(args: dict) -> dict:
        return graph_query(args.get("node", ""), args.get("relation_type", "prerequisite"))

    reg.register("course_kb_retrieval", TOOL_SCHEMAS[0], _kb_retrieval)
    reg.register("exercise_judge", TOOL_SCHEMAS[1], _exercise_judge)
    reg.register("study_record", TOOL_SCHEMAS[2], _study_record)
    reg.register("graph_query", TOOL_SCHEMAS[3], _graph_query)
    return reg
