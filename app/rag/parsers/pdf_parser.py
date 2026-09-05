# -*- coding: utf-8 -*-
"""PDF 解析器：标题感知分段

对应设计文档「PDF解析引擎优化」：强化对公式、图表关联文本、章节层级结构的识别，
按章节层级（章—节—小节）划分文本段，保证知识点拆分符合课程知识逻辑。

实现说明：
- 使用 PyMuPDF 逐块提取文本与字号，根据字号相对正文字号的增量识别标题；
- 标题行作为段落起始，正文按 chunk_size / chunk_overlap 切分；
- 输出带元数据（章节标题、页码、来源文件）的文本块，供向量化与检索使用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pymupdf  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # 旧版本兼容

logger = logging.getLogger("agent.parser.pdf")


@dataclass
class TextChunk:
    """解析出的文本块"""
    text: str
    source: str            # 来源文件名
    chapter: str = ""      # 所属章节标题（标题感知）
    page: int = 0          # 起始页码（1 起）
    resource_type: str = "教材/文献"
    video_url: str = ""    # 配套视频地址（相对路径 /videos/… 或完整 URL），无则为空
    meta: dict = field(default_factory=dict)


_HEADING_PATTERN = (
    r"^\s*(第[一二三四五六七八九十百0-9]+[章节篇讲]|[0-9一二三四五六七八九十]+(?:\.[0-9一二三四五六七八九十]+)*[、．.　\s]|"
    r"[（(][0-9一二三四五六七八九十]+[)）])"
)


def _is_heading_text(line: str) -> bool:
    """短行 + 编号/章节特征 → 判为标题"""
    import re
    line = line.strip()
    if not (2 <= len(line) <= 40):
        return False
    return bool(re.match(_HEADING_PATTERN, line))


def _detect_heading_font(page, body_size: float) -> tuple[str, float]:
    """识别当前页最大字号且高于正文的短行，返回 (标题文本, 字号)"""
    best_text, best_size = "", 0.0
    try:
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                size = max(s.get("size", 0.0) for s in spans)
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                if size >= body_size * 1.15 and _is_heading_text(text) and size > best_size:
                    best_text, best_size = text, size
    except Exception:  # noqa: BLE001
        logger.warning("PDF 标题识别异常（page %s），忽略", getattr(page, "number", "?"), exc_info=True)
    return best_text, best_size


def _split_with_overlap(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按字符数切分，带重叠，避免语义截断"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    step = max(chunk_size - overlap, chunk_size // 2)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        # 优先在句子边界切分
        end = min(start + chunk_size, len(text))
        if end < len(text):
            cut = max(text.rfind("。", start + chunk_size // 2, end),
                      text.rfind("；", start + chunk_size // 2, end),
                      text.rfind("\n", start + chunk_size // 2, end))
            if cut > start + chunk_size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)
    return [c for c in chunks if c]


# ---------------- 扫描版 PDF OCR（可选，带缓存） ----------------
_OCR_ENGINE = None


def _get_ocr_engine():
    """懒加载 OCR 引擎（rapidocr-onnxruntime）"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
        logger.info("OCR 引擎加载完成（rapidocr-onnxruntime）")
    return _OCR_ENGINE


def _ocr_page(page, dpi: int = 200) -> str:
    """将纯图像页渲染为图片并 OCR 识别文字"""
    try:
        engine = _get_ocr_engine()
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        result, _ = engine(img)
        if not result:
            return ""
        return "\n".join(item[1] for item in result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("第 %s 页 OCR 失败: %s", getattr(page, "number", "?"), exc)
        return ""


def _load_ocr_cache(cache_file: Path) -> list[str] | None:
    try:
        import json
        with cache_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pages", [])
        return pages if isinstance(pages, list) else None
    except Exception:  # noqa: BLE001
        return None


def _save_ocr_cache(cache_file: Path, pages: list[str], pdf_path: Path) -> None:
    try:
        import json
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump({"pdf": pdf_path.name, "pages": pages}, f, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR 缓存写入失败: %s", exc)


def extract_page_texts(path: Path, max_pages: int = 0, ocr: bool = False,
                       ocr_cache_file: Path | None = None) -> list[str]:
    """逐页提取文本；ocr=True 时对无文字层页面执行 OCR 并增量缓存（可断点续跑）

    Returns:
        每页文本列表（与页码一一对应，无内容页为空字符串）
    """
    doc = pymupdf.open(str(path))
    try:
        total = doc.page_count
        page_range = range(total) if not max_pages else range(min(max_pages, total))
        page_list = list(page_range)

        # 断点续跑：加载既有缓存作为起点（已识别页跳过，空文本页重试 OCR）
        pages: list[str] = []
        if ocr and ocr_cache_file is not None and ocr_cache_file.exists():
            cached = _load_ocr_cache(ocr_cache_file)
            if cached is not None and len(cached) == len(page_list):
                pages = cached
                done = sum(1 for x in pages if x.strip())
                logger.info("复用 OCR 缓存: %s（%d/%d 页已识别，断点续跑）",
                            ocr_cache_file.name, done, len(page_list))

        if len(pages) != len(page_list):
            pages = [""] * len(page_list)

        for pno in page_list:
            if pages[pno].strip():
                continue  # 已有缓存
            text = doc[pno].get_text("text")
            if not text.strip() and ocr:
                text = _ocr_page(doc[pno])
                if text.strip():
                    logger.info("第 %d 页 OCR 识别 %d 字", pno + 1, len(text))
            pages[pno] = text
            # 增量写缓存（每10页一次），中断后可续跑
            if ocr and ocr_cache_file is not None and (pno + 1) % 10 == 0:
                _save_ocr_cache(ocr_cache_file, pages, path)
        if ocr and ocr_cache_file is not None:
            _save_ocr_cache(ocr_cache_file, pages, path)
        return pages
    finally:
        doc.close()


def parse_pdf(path: str | Path, chunk_size: int = 500, chunk_overlap: int = 60,
              max_pages: int = 0, ocr: bool = False,
              ocr_cache_file: Path | None = None) -> list[TextChunk]:
    """解析 PDF 为文本块列表

    Args:
        path: PDF 文件路径
        chunk_size: 分块字符数
        chunk_overlap: 块间重叠字符数
        max_pages: 最大解析页数，0 表示全部
        ocr: 对无文字层页面（扫描版）执行 OCR
        ocr_cache_file: OCR 结果缓存文件（JSON），避免重复识别
    """
    path = Path(path)
    doc = pymupdf.open(str(path))
    try:
        total = doc.page_count
        page_range = range(total) if not max_pages else range(min(max_pages, total))
        chunks: list[TextChunk] = []
        current_chapter = ""
        body_size = 0.0

        # 扫描版 PDF：先逐页提取/OCR 文本（带缓存），作为正文与标题识别来源
        page_texts = extract_page_texts(path, max_pages=max_pages, ocr=ocr,
                                        ocr_cache_file=ocr_cache_file)
        page_list = list(page_range)

        # 估算正文字号（取前3页出现频次最高的字号）
        from collections import Counter
        sizes: Counter = Counter()
        for pno in page_list:
            try:
                d = doc[pno].get_text("dict")
                for block in d.get("blocks", []):
                    for line in block.get("lines", []):
                        for s in line.get("spans", []):
                            if s.get("text", "").strip():
                                sizes[int(round(s.get("size", 0)))] += 1
            except Exception:  # noqa: BLE001
                continue
            if len(sizes) > 3:
                break
        body_size = float(sizes.most_common(1)[0][0]) if sizes else 12.0

        section_text: list[str] = []
        for pno in page_list:
            page = doc[pno]
            heading, _ = _detect_heading_font(page, body_size)
            page_text = page_texts[pno] if pno < len(page_texts) else ""
            if not heading:
                # OCR 文本无法用字号识别标题，改用短行+编号规则识别
                for ln in page_text.splitlines():
                    ln = ln.strip()
                    if _is_heading_text(ln) and len(ln) <= 30:
                        heading = ln
                        break
            lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
            if not lines:
                continue
            if heading:
                # 遇到新章节：先输出上一章节内容
                if section_text:
                    section_joined = "\n".join(section_text)
                    for c in _split_with_overlap(section_joined, chunk_size, chunk_overlap):
                        chunks.append(TextChunk(text=c, source=path.name,
                                                chapter=current_chapter, page=pno + 1,
                                                resource_type="教材/文献"))
                    section_text = []
                current_chapter = heading
            section_text.append(page_text)
            # 防止单个大节文本无限累积
            if sum(len(t) for t in section_text) > chunk_size * 6:
                section_joined = "\n".join(section_text)
                for c in _split_with_overlap(section_joined, chunk_size, chunk_overlap):
                    chunks.append(TextChunk(text=c, source=path.name,
                                            chapter=current_chapter, page=pno + 1,
                                            resource_type="教材/文献"))
                section_text = []

        if section_text:
            section_joined = "\n".join(section_text)
            for c in _split_with_overlap(section_joined, chunk_size, chunk_overlap):
                chunks.append(TextChunk(text=c, source=path.name,
                                        chapter=current_chapter,
                                        page=min(page_range, default=0) + 1,
                                        resource_type="教材/文献"))

        logger.info("PDF 解析完成: %s → %d 块", path.name, len(chunks))
        return chunks
    finally:
        doc.close()
