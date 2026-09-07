# -*- coding: utf-8 -*-
"""数字教师 TTS 模块 —— 独立新增

功能：文本 → WAV 音频（16kHz 单声道 PCM），用于 DH_live 数字人口型同步。
实现：edge-tts（微软在线 TTS，中文效果好）+ ffmpeg（MP3→WAV 转码）。

协同开发约定：
- 本模块为独立新增，不修改既有文件；
- ffmpeg 路径自动探测（imageio-ffmpeg 自带二进制）；
- 语音角色可配置，默认 zh-CN-XiaoxiaoNeural（女声，适合林老师）。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import edge_tts

# 可用中文语音角色
VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",   # 晓晓（女声，活泼）
    "xiaoyi": "zh-CN-XiaoyiNeural",         # 晓伊（女声，温柔）
    "yunjian": "zh-CN-YunjianNeural",       # 云健（男声，沉稳）
    "yunxi": "zh-CN-YunxiNeural",           # 云希（男声，阳光）
}

DEFAULT_VOICE = "yunjian"  # 林老师（男）用沉稳男声云健，30岁左右


def _find_ffmpeg() -> str:
    """探测 ffmpeg 路径：优先 imageio-ffmpeg 自带二进制，其次系统 PATH"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        # 确保有 ffmpeg.exe 副本（部分脚本调用 "ffmpeg" 命令名）
        fdir = os.path.dirname(exe)
        standard = os.path.join(fdir, "ffmpeg.exe")
        if not os.path.exists(standard) and os.path.basename(exe) != "ffmpeg.exe":
            import shutil
            shutil.copy2(exe, standard)
        return standard
    except Exception:
        return "ffmpeg"


_FFMPEG = _find_ffmpeg()


async def text_to_wav(text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%") -> bytes:
    """文本转 WAV 音频（16kHz 单声道 PCM）

    Args:
        text: 要合成的文本
        voice: 语音角色（见 VOICES）
        rate: 语速（如 "+0%"、"-10%"、"+20%"）

    Returns:
        WAV 文件字节
    """
    voice_name = VOICES.get(voice, VOICES[DEFAULT_VOICE])

    with tempfile.TemporaryDirectory() as tmpdir:
        mp3_path = os.path.join(tmpdir, "tts.mp3")
        wav_path = os.path.join(tmpdir, "tts.wav")

        # 1. edge-tts 生成 MP3
        communicate = edge_tts.Communicate(text, voice=voice_name, rate=rate)
        await communicate.save(mp3_path)

        # 2. ffmpeg 转 WAV（16kHz 单声道，DH_live 要求）
        cmd = [
            _FFMPEG, "-y", "-i", mp3_path,
            "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le",
            wav_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 转码失败: {result.stderr.decode('utf-8', errors='ignore')[:500]}")

        with open(wav_path, "rb") as f:
            return f.read()


def text_to_wav_sync(text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%") -> bytes:
    """同步版本（用于非 async 上下文）"""
    return asyncio.run(text_to_wav(text, voice, rate))
