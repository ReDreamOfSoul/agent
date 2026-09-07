# -*- coding: utf-8 -*-
"""Embedding 向量化

支持三种后端（config.embedding.provider）：
- api  ：OpenAI 兼容 /embeddings 接口（阿里云 DashScope 兼容模式等）
- local：本地 sentence-transformers 模型（建议 Python 3.10 环境 + torch）
- none ：不启用向量检索（系统自动降级为 BM25 关键词检索，功能可用）
"""
from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger("agent.embedder")


class EmbedderError(Exception):
    pass


class BaseEmbedder:
    name = "base"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAICompatEmbedder(BaseEmbedder):
    """OpenAI 兼容向量接口"""

    name = "api"

    def __init__(self, api_base_url: str, api_key: str, model: str, dim: int = 512):
        if not api_base_url or not api_key:
            raise EmbedderError("Embedding 配置不完整：请配置 api_base_url / api_key")
        self.base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "text-embedding-v3"
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import requests
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        results: list[list[float]] = []
        # 分批调用，单批 32 条
        batch = 32
        for i in range(0, len(texts), batch):
            payload = {"model": self.model, "input": list(texts[i:i + batch])}
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                raise EmbedderError(f"Embedding 接口返回 {resp.status_code}: {resp.text[:200]}")
            data = sorted(resp.json()["data"], key=lambda x: x["index"])
            results.extend(d["embedding"] for d in data)
        return results


class LocalEmbedder(BaseEmbedder):
    """本地 sentence-transformers 模型（可选，需单独安装依赖）"""

    name = "local"

    def __init__(self, model_name: str, dim: int = 512):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise EmbedderError(
                "未安装 sentence-transformers，请执行 pip install sentence-transformers "
                "（本地模型建议使用 Python 3.10 环境）"
            ) from exc
        logger.info("加载本地 Embedding 模型: %s", model_name)
        self.model = SentenceTransformer(model_name)
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = self.model.encode(list(texts), normalize_embeddings=True)
        return [v.tolist() for v in vecs]


class NoneEmbedder(BaseEmbedder):
    """未配置向量服务：返回空，检索模块自动降级为纯关键词检索"""

    name = "none"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return []


def create_embedder(cfg) -> BaseEmbedder:
    emb_cfg = cfg.get("embedding", {}) or {}
    provider = emb_cfg.get("provider", "none")
    if provider == "api":
        return OpenAICompatEmbedder(
            api_base_url=emb_cfg.get("api_base_url", ""),
            api_key=emb_cfg.get("api_key", ""),
            model=emb_cfg.get("api_model", ""),
            dim=emb_cfg.get("dim", 512),
        )
    if provider == "local":
        return LocalEmbedder(
            model_name=emb_cfg.get("local_model_name", "BAAI/bge-small-zh-v1.5"),
            dim=emb_cfg.get("dim", 512),
        )
    if provider == "none":
        return NoneEmbedder()
    raise EmbedderError(f"未知的 Embedding provider: {provider}")
