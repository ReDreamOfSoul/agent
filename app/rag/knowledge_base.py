# -*- coding: utf-8 -*-
"""知识库：构建 / 存储 / 混合检索

对应设计文档第 5 章「知识库设计」：
- 知识源：教材 PDF、十章课件、题库、教学大纲；
- 解析分段：标题感知、重叠分块、知识点打标（章节标签）；
- 向量化与索引：可选向量库 + BM25 关键词索引；
- 检索与重排：混合检索（向量+BM25）→ 重排 → 阈值判定。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np

from .bm25 import BM25Index
from .embedder import BaseEmbedder, NoneEmbedder
from .parsers.doc_parser import parse_doc
from .parsers.pdf_parser import TextChunk, parse_pdf
from .parsers.ppt_parser import parse_ppt

logger = logging.getLogger("agent.rag.kb")

CHUNKS_FILE = "chunks.json"
BM25_FILE = "bm25.pkl"
VEC_FILE = "vectors.npz"


class KnowledgeBase:
    """课程知识库（单文件式索引存储，无外部数据库依赖）"""

    def __init__(self, source_dir: str | Path, index_dir: str | Path,
                 embedder: BaseEmbedder | None = None,
                 chunk_size: int = 500, chunk_overlap: int = 60,
                 pdf_max_pages: int = 0, pdf_ocr: bool = False,
                 ocr_cache_dir: str | Path | None = None):
        self.source_dir = Path(source_dir)
        self.index_dir = Path(index_dir)
        self.embedder = embedder or NoneEmbedder()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.pdf_max_pages = pdf_max_pages
        self.pdf_ocr = pdf_ocr
        self.ocr_cache_dir = Path(ocr_cache_dir) if ocr_cache_dir else None

        self.chunks: list[TextChunk] = []
        self.bm25 = BM25Index()
        self.vectors: np.ndarray | None = None  # (N, dim)
        self._has_vector = False

    # ---------- 构建 ----------
    def parse_all(self) -> list[TextChunk]:
        """解析 source_dir 下全部支持类型的文件"""
        all_chunks: list[TextChunk] = []
        files = sorted(
            p for p in self.source_dir.rglob("*")
            if p.suffix.lower() in (".pdf", ".ppt", ".pptx", ".doc", ".docx")
        )
        if not files:
            raise FileNotFoundError(
                f"知识库源目录为空或没有受支持的文件: {self.source_dir}（支持 pdf/ppt/pptx/doc/docx）"
            )
        for f in files:
            try:
                if f.suffix.lower() == ".pdf":
                    ocr_cache = None
                    if self.pdf_ocr and self.ocr_cache_dir is not None:
                        ocr_cache = self.ocr_cache_dir / (f.stem + ".ocr.json")
                    chunks = parse_pdf(f, self.chunk_size, self.chunk_overlap,
                                       self.pdf_max_pages, ocr=self.pdf_ocr,
                                       ocr_cache_file=ocr_cache)
                elif f.suffix.lower() in (".ppt", ".pptx"):
                    chunks = parse_ppt(f, self.chunk_size, self.chunk_overlap)
                else:
                    chunks = parse_doc(f, self.chunk_size, self.chunk_overlap)
                all_chunks.extend(chunks)
                logger.info("解析 %s → %d 块", f.name, len(chunks))
            except Exception as exc:  # noqa: BLE001
                logger.error("解析 %s 失败: %s", f.name, exc)

        # 视频课程：读取 video/manifest.json 生成视频知识块（可被检索命中，携带播放地址）
        try:
            video_dir = self.source_dir / "video"
            manifest_file = video_dir / "manifest.json"
            if manifest_file.exists():
                vdata = json.loads(manifest_file.read_text(encoding="utf-8"))
                for v in vdata.get("videos", []):
                    vid = v.get("id", "") or Path(v.get("video_url", "")).stem
                    title = v.get("title", "") or vid
                    chapter = v.get("chapter", "")
                    desc = v.get("description", "") or ""
                    vurl = v.get("video_url", "")
                    text = f"【视频课程】{title}\n所属章节：{chapter}"
                    if desc:
                        text += f"\n简介：{desc}"
                    all_chunks.append(TextChunk(
                        text=text, source=f"视频/{vid}", chapter=chapter,
                        resource_type="视频", video_url=vurl,
                        meta={"video_id": vid, "description": desc},
                    ))
                logger.info("视频课程块: %d 个（manifest=%s）",
                            len(vdata.get("videos", [])), manifest_file.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("视频清单解析失败（不影响主流程）: %s", exc)

        if not all_chunks:
            raise RuntimeError("知识库解析结果为空，请检查源文件是否可读")
        return all_chunks

    def build(self) -> dict:
        """完整构建：解析 → 分词索引 → 向量化（如配置）"""
        t0 = time.time()
        self.chunks = self.parse_all()
        texts = [c.text for c in self.chunks]
        self.bm25.build(texts)

        stats = {"chunks": len(self.chunks), "build_seconds": round(time.time() - t0, 2)}

        if not isinstance(self.embedder, NoneEmbedder):
            try:
                vecs = self.embedder.embed(texts)
                self.vectors = np.asarray(vecs, dtype=np.float32)
                if self.vectors.ndim == 1:
                    self.vectors = self.vectors.reshape(1, -1)
                self._has_vector = True
                stats["vector_dim"] = int(self.vectors.shape[1])
                logger.info("向量化完成: %d 条 × %d 维", len(self.vectors), self.vectors.shape[1])
            except Exception as exc:  # noqa: BLE001
                logger.warning("向量化失败，降级为纯关键词检索: %s", exc)
                self._has_vector = False
        return stats

    # ---------- 存储 ----------
    def save(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        dump = [asdict(c) for c in self.chunks]
        with (self.index_dir / CHUNKS_FILE).open("w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False)
        self.bm25.save(self.index_dir / BM25_FILE)
        if self._has_vector and self.vectors is not None:
            np.savez(self.index_dir / VEC_FILE, vectors=self.vectors)
        logger.info("知识库索引已保存至 %s", self.index_dir)

    def load(self) -> bool:
        """加载已构建索引，成功返回 True"""
        chunks_file = self.index_dir / CHUNKS_FILE
        bm25_file = self.index_dir / BM25_FILE
        if not chunks_file.exists() or not bm25_file.exists():
            return False
        with chunks_file.open("r", encoding="utf-8") as f:
            self.chunks = [TextChunk(**d) for d in json.load(f)]
        self.bm25 = BM25Index.load(bm25_file)
        vec_file = self.index_dir / VEC_FILE
        if vec_file.exists():
            try:
                self.vectors = np.load(vec_file)["vectors"]
                self._has_vector = True
            except Exception:  # noqa: BLE001
                self._has_vector = False
        logger.info("知识库索引已加载: %d 块, 向量=%s", len(self.chunks), self._has_vector)
        return True

    # ---------- 检索 ----------
    def _cosine_scores(self, query_vec: np.ndarray) -> np.ndarray:
        if self.vectors is None:
            return np.zeros(len(self.chunks), dtype=np.float32)
        q = query_vec / (np.linalg.norm(query_vec) + 1e-9)
        dots = self.vectors @ q
        return dots

    def retrieve(self, query: str, top_k: int = 5, threshold: float = 0.72,
                 threshold_keyword: float | None = None,
                 vector_weight: float = 0.6, keyword_weight: float = 0.4) -> dict:
        """混合检索 + 重排 + 阈值判定

        Args:
            query: 用户问题
            top_k: 返回片段数
            threshold: 向量/混合模式命中阈值（余弦相似度语义）
            threshold_keyword: 纯关键词模式的命中阈值（None 时等于 threshold）
            vector_weight / keyword_weight: 混合权重

        Returns:
            {
              "query": ...,
              "hit": bool,          # 是否判定为知识库命中（最高分 >= 有效阈值）
              "best_score": float,  # 归一化最高分（0~1）
              "results": [{"chunk": TextChunk, "score": float, "vector_score": float, "keyword_score": float}, ...]
            }
        """
        n = len(self.chunks)
        if n == 0:
            return {"query": query, "hit": False, "best_score": 0.0, "results": []}

        # 关键词得分（相对归一化 × idf 加权查询覆盖率，惩罚未收录术语、剔除提问虚词）
        from .bm25 import tokenize
        q_tokens = tokenize(query)
        total_w = self.bm25.query_weight(q_tokens)
        kw_hits = self.bm25.search(query, top_k=max(top_k * 3, 10))
        kw_scores = np.zeros(n, dtype=np.float32)
        kw_coverage = np.zeros(n, dtype=np.float32)
        for idx, s, mw in kw_hits:
            kw_scores[idx] = s
            kw_coverage[idx] = mw / total_w if total_w > 0 else 0.0
        kw_norm = kw_scores / (kw_scores.max() + 1e-9) if kw_scores.max() > 0 else kw_scores
        kw_signal = kw_norm * kw_coverage

        # 向量得分（若可用）
        vec_scores = np.zeros(n, dtype=np.float32)
        if self._has_vector:
            qvec = self.embedder.embed([query])
            if qvec:
                vec_scores = self._cosine_scores(np.asarray(qvec[0], dtype=np.float32))
                # 余弦相似度可能为负，平移至 [0,1]
                vec_scores = np.clip((vec_scores + 1.0) / 2.0, 0.0, 1.0)

        wv = vector_weight if self._has_vector else 0.0
        wk = keyword_weight if self._has_vector else 1.0
        if wv + wk <= 0:
            wv, wk = 0.0, 1.0
        hybrid = wv * vec_scores + wk * kw_signal

        order = np.argsort(-hybrid)[:top_k]
        best = float(hybrid[order[0]]) if len(order) else 0.0
        # 命中阈值：纯关键词模式语义不同于向量相似度，使用独立阈值（默认更低）
        eff_threshold = threshold
        if not self._has_vector and threshold_keyword is not None:
            eff_threshold = threshold_keyword
        results = [
            {
                "chunk": self.chunks[i],
                "score": round(float(hybrid[i]), 4),
                "vector_score": round(float(vec_scores[i]), 4) if self._has_vector else None,
                "keyword_score": round(float(kw_signal[i]), 4),
            }
            for i in order if hybrid[i] > 0
        ]
        return {
            "query": query,
            "hit": best >= eff_threshold and bool(results),
            "best_score": round(best, 4),
            "threshold": eff_threshold,
            "results": results,
            "retrieval_mode": "hybrid" if self._has_vector else "keyword_only",
        }


def chunks_to_text(results: list[dict]) -> list[str]:
    """将检索结果转换为纯文本片段列表（供 LLM 上下文使用）"""
    return [r["chunk"].text for r in results]
