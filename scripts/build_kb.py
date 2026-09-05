# -*- coding: utf-8 -*-
"""知识库构建脚本

用法：
    python scripts/build_kb.py                 # 解析 data/knowledge_base 并构建索引
    python scripts/build_kb.py --rebuild       # 强制全量重建（忽略已有索引）
    python scripts/build_kb.py --pdf-pages 100 # 限制每个PDF解析前100页（快速试运行）

说明：
- 源文件放入 data/knowledge_base/（支持 pdf/ppt/pptx/doc/docx）；
- 默认检索降级为 BM25 关键词模式；配置 Embedding 服务（.env）后自动启用混合检索。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_config  # noqa: E402
from app.rag.embedder import create_embedder  # noqa: E402
from app.rag.knowledge_base import KnowledgeBase  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("build_kb")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建课程智能体知识库索引")
    parser.add_argument("--rebuild", action="store_true", help="强制全量重建")
    parser.add_argument("--pdf-pages", type=int, default=0, help="每个PDF最多解析页数（0=全部）")
    parser.add_argument("--no-ocr", action="store_true", help="跳过扫描版PDF的OCR识别")
    args = parser.parse_args()

    cfg = load_config()
    kb_cfg = cfg.get("knowledge_base", {}) or {}
    source_dir = Path(kb_cfg.get("source_dir", "data/knowledge_base"))
    index_dir = Path(kb_cfg.get("index_dir", "data/index"))
    if not source_dir.is_absolute():
        source_dir = Path(__file__).resolve().parents[1] / source_dir
    if not index_dir.is_absolute():
        index_dir = Path(__file__).resolve().parents[1] / index_dir

    if not source_dir.exists():
        logger.error("知识库源目录不存在: %s", source_dir)
        return 1

    if not args.rebuild and (index_dir / "chunks.json").exists():
        logger.info("索引已存在: %s（如需重建请加 --rebuild）", index_dir)
        return 0

    embedder = create_embedder(cfg)
    kb = KnowledgeBase(
        source_dir, index_dir, embedder=embedder,
        chunk_size=kb_cfg.get("chunk_size", 500),
        chunk_overlap=kb_cfg.get("chunk_overlap", 60),
        pdf_max_pages=args.pdf_pages or kb_cfg.get("pdf_max_pages", 0),
        pdf_ocr=(not args.no_ocr) and kb_cfg.get("pdf_ocr", False),
        ocr_cache_dir=kb_cfg.get("ocr_cache_dir", "data/cache"),
    )
    logger.info("开始解析知识库: %s", source_dir)
    stats = kb.build()
    kb.save()
    logger.info("构建完成: %s", stats)
    print("\n=== 构建结果 ===")
    print(f"知识块数量 : {stats['chunks']}")
    print(f"构建耗时   : {stats['build_seconds']} 秒")
    print(f"检索模式   : {'混合检索(向量+BM25)' if kb._has_vector else '关键词检索(BM25)'}")
    if stats.get("vector_dim"):
        print(f"向量维度   : {stats['vector_dim']}")
    print(f"索引保存至 : {index_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
