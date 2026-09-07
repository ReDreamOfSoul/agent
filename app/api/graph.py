# -*- coding: utf-8 -*-
"""知识图谱接口：GET /v1/graph/overview（章节总览）、GET /v1/graph（知识点关系查询）"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..core.graph import graph_overview, graph_query, load_network_graph, network_query

router = APIRouter(prefix="/v1", tags=["知识图谱"])


@router.get("/graph/overview", summary="章节知识图谱总览")
def overview() -> dict:
    return {"chapters": graph_overview()}


@router.get("/graph", summary="知识点前置/后继/目标关系查询")
def graph(node: str = Query(..., min_length=1, description="知识点或章节名称（模糊匹配）"),
          relation_type: str = Query("prerequisite",
                                     description="关系类型：prerequisite 前置 | successor 后继 | objective 学习目标")) -> dict:
    return graph_query(node, relation_type)

@router.get("/graph/network", summary="网状知识图谱（力导向图数据）")
def network_graph() -> dict:
    return load_network_graph()


@router.get("/graph/node", summary="查询图谱节点的邻居关系")
def graph_node(node_id: str = Query(..., description="节点ID或名称")) -> dict:
    return network_query(node_id)
