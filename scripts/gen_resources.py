# -*- coding: utf-8 -*-
"""生成课程资源清单 data/resources.json

扫描知识库目录，将课件（PPT/PPTX）、教材（PDF）、题库、教学大纲、教学视频
整理为统一资源清单，按章节关联，供智能体"要资料"意图返回下载链接。

用法：
    python scripts/gen_resources.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config  # noqa: E402

KB_DIR = PROJECT_ROOT / "data" / "knowledge_base"
VIDEO_DIR = KB_DIR / "video"
OUTPUT = PROJECT_ROOT / "data" / "resources.json"

PPT_EXTS = {".ppt", ".pptx"}
PDF_EXTS = {".pdf"}
DOC_EXTS = {".doc", ".docx"}

_CN_NUMS = "零一二三四五六七八九十"


def _cn_num(n: int) -> str:
    return _CN_NUMS[n] if 1 <= n <= 10 else str(n)


def _chapter_name(cfg, num: int) -> str:
    chapters = cfg.get("course.chapters", [])
    if 1 <= num <= len(chapters):
        return f"第{_cn_num(num)}章 {chapters[num - 1]}"
    return f"第{_cn_num(num)}章"


def _parse_ppt_chapter(filename: str, cfg) -> str | None:
    """从 PPT 文件名推断章节，如 '第三章 机器人运动学.pptx' → '第三章 机器人运动学'"""
    m = re.match(r"第([一二三四五六七八九十\d]+)章[\s·]*(.+?)\.(ppt|pptx)$", filename, re.I)
    if not m:
        return None
    num_str = m.group(1)
    # 中文数字转阿拉伯
    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    num = cn_map.get(num_str)
    if num is None:
        try:
            num = int(num_str)
        except ValueError:
            return None
    return _chapter_name(cfg, num)


def main() -> None:
    cfg = load_config()
    if not KB_DIR.exists():
        print(f"知识库目录不存在: {KB_DIR}")
        sys.exit(1)

    resources: list[dict] = []
    rid = 0

    def _add(title: str, rtype: str, chapter: str, filename: str, url: str, desc: str = "") -> None:
        nonlocal rid
        rid += 1
        resources.append({
            "id": f"res_{rid:03d}",
            "title": title,
            "type": rtype,
            "chapter": chapter,
            "file": filename,
            "url": url,
            "description": desc,
        })

    # 1) 课件 PPT/PPTX
    for f in sorted(KB_DIR.iterdir()):
        if f.suffix.lower() in PPT_EXTS:
            chapter = _parse_ppt_chapter(f.name, cfg) or ""
            title = f"{chapter}（课件）" if chapter else f"{f.stem}（课件）"
            _add(title, "ppt", chapter, f.name, f"/resources/{f.name}", "课程课件")

    # 2) 教材 PDF
    for f in sorted(KB_DIR.iterdir()):
        if f.suffix.lower() in PDF_EXTS:
            _add(f"{f.stem}（教材）", "pdf", "", f.name, f"/resources/{f.name}",
                 "课程教材，刘极峰、杨小兰，高等教育出版社2019")

    # 3) 题库 DOC
    for f in sorted(KB_DIR.iterdir()):
        if f.suffix.lower() in DOC_EXTS and "题库" in f.name:
            _add(f"{f.stem}（题库）", "exercise", "", f.name, f"/resources/{f.name}", "课程题库（含参考答案）")

    # 4) 教学大纲 DOC
    for f in sorted(KB_DIR.iterdir()):
        if f.suffix.lower() in DOC_EXTS and "大纲" in f.name:
            _add(f"{f.stem}（教学大纲）", "syllabus", "", f.name, f"/resources/{f.name}", "课程教学大纲")

    # 5) 教学视频（复用 video/manifest.json）
    manifest_file = VIDEO_DIR / "manifest.json"
    if manifest_file.exists():
        vdata = json.loads(manifest_file.read_text(encoding="utf-8"))
        for v in vdata.get("videos", []):
            _add(v.get("title", ""), "video", v.get("chapter", ""),
                 f"video/{Path(v.get('video_url', '')).name}",
                 v.get("video_url", ""), v.get("description", "") or "教学视频")

    OUTPUT.write_text(
        json.dumps({"updated_at": __import__("datetime").date.today().isoformat(),
                    "total": len(resources), "resources": resources},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"资源清单已生成: {OUTPUT}（{len(resources)} 个资源）")
    for r in resources:
        print(f"  - [{r['type']:4s}] {r['title']} | {r['chapter'] or '全局'} | {r['url']}")


if __name__ == "__main__":
    main()
