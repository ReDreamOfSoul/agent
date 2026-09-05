# -*- coding: utf-8 -*-
"""生成/更新视频课程清单 manifest.json

扫描 知识库/video 目录下的视频文件，按文件名（ch{N}_{M}.mp4）推断所属章节，
生成或更新 data/knowledge_base/video/manifest.json。已存在的条目（含人工补充的
标题/描述/时长）会被保留；新增视频文件会自动补入清单。

用法：
    python scripts/gen_video_manifest.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config  # noqa: E402

VIDEO_DIR_REL = "data/knowledge_base/video"
MANIFEST = "manifest.json"

# 章节序号（config.yaml course.chapters）→ 章标题
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".m4v"}


_CN_NUMS = "零一二三四五六七八九"


def _cn_num(n: int) -> str:
    if 1 <= n <= 10:
        return _CN_NUMS[n]
    return str(n)


def _chapter_name(cfg, num: int) -> str:
    chapters = cfg.get("course.chapters", [])
    if 1 <= num <= len(chapters):
        return f"第{_cn_num(num)}章 {chapters[num - 1]}"
    return f"第{_cn_num(num)}章"


def _parse_filename(name: str) -> tuple[int, int] | None:
    m = re.match(r"ch(\d+)(?:_(\d+))?", name, re.I)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 1)


def main() -> None:
    cfg = load_config()
    video_dir = PROJECT_ROOT / VIDEO_DIR_REL
    if not video_dir.exists():
        print(f"视频目录不存在: {video_dir}")
        sys.exit(1)

    manifest_path = video_dir / MANIFEST
    existing: dict[str, dict] = {}
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = {v["id"]: v for v in data.get("videos", [])}

    files = sorted(p for p in video_dir.iterdir()
                   if p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("."))
    if not files:
        print("未发现视频文件")
        sys.exit(1)

    videos: list[dict] = []
    for f in files:
        parsed = _parse_filename(f.stem)
        if parsed is None:
            print(f"跳过（无法识别章节）: {f.name}")
            continue
        ch_no, seq = parsed
        vid = f.stem
        ch_name = _chapter_name(cfg, ch_no)
        old = existing.get(vid, {})
        videos.append({
            "id": vid,
            "title": old.get("title") or f"{ch_name} 教学视频（{seq}）",
            "chapter": old.get("chapter") or ch_name,
            "description": old.get("description") or "",
            "video_url": old.get("video_url") or f"/videos/{f.name}",
            "duration": old.get("duration") or "",
        })

    manifest_path.write_text(
        json.dumps({"updated_at": __import__("datetime").date.today().isoformat(),
                    "videos": videos}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"视频清单已更新: {manifest_path}（{len(videos)} 个视频）")
    for v in videos:
        print(f"  - {v['id']}: {v['title']} [{v['chapter']}] -> {v['video_url']}")


if __name__ == "__main__":
    main()
