# -*- coding: utf-8 -*-
"""教师端接口：教学数据导出 GET /v1/export/{table}（CSV，Excel 可直接打开）"""
from __future__ import annotations

import csv
import io
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..core.study_store import StudyStore
from .deps import get_study

router = APIRouter(prefix="/v1", tags=["教师"])


@router.get("/export/{table}", summary="教学数据导出（CSV）")
def export(table: str, days: int = Query(30, ge=1, le=365, description="导出最近天数"),
           study: StudyStore = Depends(get_study)) -> Response:
    try:
        cols, rows = study.export_rows(table, time.time() - days * 86400)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM，保证 Excel 打开中文不乱码
    writer = csv.writer(buf)
    writer.writerow(cols)
    for r in rows:
        writer.writerow(["" if v is None else str(v).replace("\n", " ").replace("\r", " ") for v in r])

    fname = f"{table}_{time.strftime('%Y%m%d')}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
