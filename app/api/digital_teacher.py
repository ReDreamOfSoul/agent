# -*- coding: utf-8 -*-
"""数字教师 API 路由 —— 独立新增模块

接口：
    GET  /v1/digital-teacher/teachers          教师角色清单
    POST /v1/digital-teacher/start             以主题开启研讨（检索知识库 + 教师研讨）
    POST /v1/digital-teacher/chat              用户插入发言，教师团队回应一轮
    GET  /v1/digital-teacher/{session_id}      查询研讨记录

协同开发约定：
- 本模块通过 request.app.state 复用公共对象 kb / llm，不修改既有文件；
- 引擎懒加载为模块级单例（单进程 uvicorn 下安全），注册入口见 app/main.py。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
import json
from pydantic import BaseModel, Field

from ..core.digital_teacher import create_engine, create_tutor_engine

logger = logging.getLogger("agent.api.digital_teacher")

router = APIRouter(prefix="/v1/digital-teacher", tags=["数字教师"])

_engine = None


def _get_engine(request: Request):
    """懒加载研讨引擎（复用 app.state 上的 kb / llm）"""
    global _engine
    if _engine is None:
        kb = getattr(request.app.state, "kb", None)
        llm = getattr(request.app.state, "llm", None)
        if kb is None or llm is None:
            raise HTTPException(status_code=503, detail="知识库或大模型未就绪")
        _engine = create_engine(kb, llm)
    return _engine


class StartRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500, description="研讨主题/问题")
    rounds: int = Field(1, ge=1, le=3, description="研讨轮数（每轮含 2 位教师+主持人总结）")


class JoinRequest(BaseModel):
    session_id: str = Field(..., description="研讨会话ID")
    message: str = Field(..., min_length=1, max_length=2000, description="用户发言")


@router.get("/teachers", summary="数字教师角色清单")
def list_teachers(request: Request) -> dict:
    return {"teachers": _get_engine(request).list_teachers()}


@router.post("/start", summary="开启数字教师研讨")
def start(req: StartRequest, request: Request) -> dict:
    try:
        return _get_engine(request).start(req.topic, rounds=req.rounds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("数字教师研讨启动失败")
        raise HTTPException(status_code=500, detail=f"数字教师服务异常: {exc}") from exc


@router.post("/chat", summary="用户插入发言，教师团队回应")
def join(req: JoinRequest, request: Request) -> dict:
    try:
        return _get_engine(request).join(req.session_id, req.message)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("数字教师回应失败")
        raise HTTPException(status_code=500, detail=f"数字教师服务异常: {exc}") from exc


@router.get("/hello", summary="数字教师欢迎语（前端语音播报用）")
def hello() -> dict:
    from ..core.digital_teacher import TUTOR, TUTOR_WELCOME
    return {"teacher": dict(TUTOR), "reply": TUTOR_WELCOME}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/talk/stream", summary="数字教师一对一对话（SSE 流式，边生成边推送）")
def talk_stream(req: TalkRequest, request: Request) -> StreamingResponse:
    """SSE 事件：delta（逐句文本）→ done（携带 session_id）；异常发 error"""
    tutor = _get_tutor(request)

    def gen():
        try:
            g = tutor.talk_stream(req.message, req.session_id)
            while True:
                try:
                    piece = next(g)
                except StopIteration as si:
                    yield _sse("done", {"session_id": si.value or ""})
                    return
                yield _sse("delta", {"text": piece})
        except KeyError as exc:
            yield _sse("error", {"detail": str(exc)})
        except ValueError as exc:
            yield _sse("error", {"detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("数字教师流式对话失败")
            yield _sse("error", {"detail": f"数字教师服务异常: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/{session_id}", summary="查询研讨记录")
def get_session(session_id: str, request: Request) -> dict:
    try:
        return _get_engine(request).get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ================================================================
# 一对一数字教师（AI 形象 + 语音互动）
# ================================================================

_tutor_engine = None


def _get_tutor(request: Request):
    """懒加载一对一数字教师引擎（复用 app.state 上的 kb / llm）"""
    global _tutor_engine
    if _tutor_engine is None:
        kb = getattr(request.app.state, "kb", None)
        llm = getattr(request.app.state, "llm", None)
        if kb is None or llm is None:
            raise HTTPException(status_code=503, detail="知识库或大模型未就绪")
        _tutor_engine = create_tutor_engine(kb, llm)
    return _tutor_engine


class TalkRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="学生提问")
    session_id: str | None = Field(None, description="会话ID（不传则新开会话，传错则返回404）")


@router.post("/asr", summary="语音转文字（本地离线识别，Vosk 中文，无需联网）")
async def asr(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="未收到音频数据")
    try:
        from ..core.speech import get_recognizer
        text = get_recognizer().recognize(data)
        return {"text": text}
    except Exception as exc:  # noqa: BLE001
        logger.exception("语音识别失败")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {exc}") from exc


@router.post("/talk", summary="数字教师一对一对话（知识库问答，可语音朗读）")
def talk(req: TalkRequest, request: Request) -> dict:
    try:
        return _get_tutor(request).talk(req.message, req.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("数字教师对话失败")
        raise HTTPException(status_code=500, detail=f"数字教师服务异常: {exc}") from exc


# ================================================================
# TTS 语音合成（用于 DH_live 数字人口型同步）
# ================================================================

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="要合成的文本")
    voice: str = Field("xiaoyi", description="语音角色: xiaoxiao/xiaoyi/yunjian/yunxi")
    rate: str = Field("+0%", description="语速，如 +0% / -10% / +20%")


@router.post("/tts", summary="文本转 WAV 音频（16kHz 单声道，用于数字人口型同步）")
async def tts(req: TTSRequest) -> StreamingResponse:
    try:
        from ..core.tts import text_to_wav
        wav_bytes = await text_to_wav(req.text, voice=req.voice, rate=req.rate)
        return StreamingResponse(
            iter([wav_bytes]),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=tts.wav"}
        )
    except Exception as exc:
        logger.exception("TTS 合成失败")
        raise HTTPException(status_code=500, detail=f"TTS 合成失败: {exc}") from exc
