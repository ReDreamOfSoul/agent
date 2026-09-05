# -*- coding: utf-8 -*-
"""PPT/PPTX 解析器：按幻灯片提取文本，幻灯片标题作为章节标签

对应设计文档「多媒体格式适配」：课件文本经本地离线解析分段后入库，
规避学校平台知识库不支持 PPT 上传的限制。
"""
from __future__ import annotations

import logging
from pathlib import Path

from pptx import Presentation

from .pdf_parser import TextChunk, _split_with_overlap

logger = logging.getLogger("agent.parser.ppt")


def _shape_text(shape) -> str:
    """递归提取形状内文本（含表格、组合形状）"""
    texts: list[str] = []
    if shape.shape_type == 6:  # GROUP
        for sub in shape.shapes:
            texts.append(_shape_text(sub))
        return "\n".join(t for t in texts if t)
    if shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            line = "".join(run.text for run in para.runs)
            if line.strip():
                texts.append(line.strip())
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                texts.append(" | ".join(cells))
    return "\n".join(texts)


def _convert_ppt_to_pptx(src: Path, tmp_dir: Path) -> Path | None:
    """旧版 .ppt（二进制格式）→ .pptx：优先 PowerPoint COM，其次 LibreOffice"""
    dst = tmp_dir / (src.stem + ".pptx")
    # 1) PowerPoint COM（Windows + 已装 Office）
    try:
        import win32com.client
        app = win32com.client.Dispatch("PowerPoint.Application")
        try:
            pres = app.Presentations.Open(str(src), ReadOnly=True, WithWindow=False)
            try:
                pres.SaveAs(str(dst), 24)  # ppSaveAsOpenXMLPresentation
            finally:
                pres.Close()
            logger.info("PowerPoint COM 转换成功: %s → %s", src.name, dst.name)
            return dst
        finally:
            app.Quit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PowerPoint COM 转换失败: %s", exc)
    # 2) LibreOffice（Linux / 未装 Office）
    try:
        import subprocess
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pptx", "--outdir", str(tmp_dir), str(src)],
            check=True, capture_output=True, timeout=180,
        )
        if dst.exists():
            logger.info("LibreOffice 转换成功: %s → %s", src.name, dst.name)
            return dst
    except Exception as exc:  # noqa: BLE001
        logger.warning("LibreOffice 转换失败: %s", exc)
    return None


def parse_ppt(path: str | Path, chunk_size: int = 500, chunk_overlap: int = 60) -> list[TextChunk]:
    """解析 PPT/PPTX：每页为单元，标题取首页文本框，正文按块切分

    旧版 .ppt 自动经 PowerPoint COM / LibreOffice 转为 .pptx 后解析。
    """
    import tempfile

    path = Path(path)
    parse_path = path
    tmp_dir: Path | None = None
    if path.suffix.lower() == ".ppt":
        tmp_dir = Path(tempfile.mkdtemp(prefix="ppt_conv_"))
        converted = _convert_ppt_to_pptx(path, tmp_dir)
        if converted is None:
            logger.error("旧版 .ppt 无法转换（需本机 Office 或 LibreOffice）: %s", path.name)
            return []
        parse_path = converted
    try:
        prs = Presentation(str(parse_path))
        chunks: list[TextChunk] = []
        for slide_index, slide in enumerate(prs.slides, start=1):
            slide_texts: list[str] = []
            title = ""
            for shape in slide.shapes:
                txt = _shape_text(shape)
                if not txt.strip():
                    continue
                slide_texts.append(txt)
                if not title and len(txt.splitlines()[0]) <= 40:
                    title = txt.splitlines()[0].strip()
            full = "\n".join(slide_texts)
            if not full.strip():
                continue
            for c in _split_with_overlap(full, chunk_size, chunk_overlap):
                chunks.append(TextChunk(
                    text=c, source=path.name, chapter=title,
                    page=slide_index, resource_type="课件",
                    meta={"slide": slide_index},
                ))
        logger.info("PPT 解析完成: %s → %d 块", path.name, len(chunks))
        return chunks
    finally:
        if tmp_dir is not None:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
