# -*- coding: utf-8 -*-
"""课程知识图谱（章节级）

双图谱（知识图谱 / 目标图谱）的最小可用实现：
- 以课程十章的教学顺序提供前置 / 后继知识点关系；
- 目标图谱提供每章学习目标。
图谱完整数据（知识点级先修关系）建设后，可替换本模块的查询实现而不改调用方。
MCP 工具与 REST 接口共用本模块，避免关系数据重复定义。
"""
from __future__ import annotations

# 章节教学顺序（与 config.yaml course.chapters 一致）
CHAPTER_SEQ = [
    "绪论", "机器人本体结构", "机器人运动学", "机器人动力学", "机器人轨迹规划",
    "机器人控制系统", "机器人的编程与语言", "工业机器人", "操纵型机器人", "仿生机器人",
]


def _locate(node: str) -> int | None:
    """模糊匹配章节（双向包含），返回章节序号"""
    node = (node or "").strip()
    if not node:
        return None
    return next((i for i, c in enumerate(CHAPTER_SEQ) if c in node or node in c), None)


def graph_overview() -> list[dict]:
    """章节图谱总览（含前置/后继章节名）"""
    out: list[dict] = []
    for i, name in enumerate(CHAPTER_SEQ):
        out.append({
            "index": i,
            "name": name,
            "prerequisite": CHAPTER_SEQ[i - 1] if i > 0 else None,
            "successor": CHAPTER_SEQ[i + 1] if i < len(CHAPTER_SEQ) - 1 else None,
            "objective": f"掌握《{name}》核心概念与典型应用",
        })
    return out


def graph_query(node: str, relation_type: str = "prerequisite") -> dict:
    """查询某知识点（章节）的前置 / 后继 / 学习目标

    Args:
        node: 知识点或章节名称（支持模糊包含匹配）
        relation_type: prerequisite | successor | objective
    """
    idx = _locate(node)
    result: dict = {"node": node, "matched_chapter": CHAPTER_SEQ[idx] if idx is not None else None,
                    "relation_type": relation_type, "related": []}
    if idx is None:
        return result
    if relation_type == "prerequisite" and idx > 0:
        result["related"] = [{"name": CHAPTER_SEQ[idx - 1], "type": "前置知识点"}]
    elif relation_type == "successor" and idx < len(CHAPTER_SEQ) - 1:
        result["related"] = [{"name": CHAPTER_SEQ[idx + 1], "type": "后继知识点"}]
    elif relation_type == "objective":
        result["related"] = [{"name": f"掌握《{CHAPTER_SEQ[idx]}》核心概念与典型应用", "type": "学习目标"}]
    return result

# ==================== 网状知识图谱（小节级） ====================
import os as _os
import json as _json

_NETWORK_CACHE = None

def load_network_graph() -> dict:
    """加载网状知识图谱（章节→小节→知识点 + 关系边）"""
    global _NETWORK_CACHE
    if _NETWORK_CACHE is not None:
        return _NETWORK_CACHE
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                          "..", "data", "knowledge_graph.json")
    path = _os.path.normpath(path)
    if _os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _NETWORK_CACHE = _json.load(f)
    else:
        _NETWORK_CACHE = {"nodes": [], "links": [], "meta": {}}
    return _NETWORK_CACHE


def network_query(node_id: str) -> dict:
    """查询某节点的邻居关系（前置/后继/相关/包含）"""
    g = load_network_graph()
    node = next((n for n in g["nodes"] if n["id"] == node_id or n["name"] == node_id), None)
    if not node:
        return {"node": None, "neighbors": []}
    neighbors = []
    for link in g["links"]:
        if link["source"] == node["id"]:
            target = next((n for n in g["nodes"] if n["id"] == link["target"]), None)
            if target:
                neighbors.append({"direction": "out", "type": link["type"], "node": target})
        elif link["target"] == node["id"]:
            source = next((n for n in g["nodes"] if n["id"] == link["source"]), None)
            if source:
                neighbors.append({"direction": "in", "type": link["type"], "node": source})
    return {"node": node, "neighbors": neighbors}
