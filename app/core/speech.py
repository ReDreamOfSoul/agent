# -*- coding: utf-8 -*-
"""本地离线语音识别（sherpa-onnx Paraformer 中文 主引擎 + Vosk 后备）—— 独立新增模块

用途：数字教师「语音输入」的后端识别。
前端录制 PCM WAV 上传，本模块在本地离线转文字，不依赖任何在线语音服务
（中国大陆网络可用），无需 API Key。

引擎选择：
- 主：sherpa-onnx Paraformer 中文小模型（int8 量化，~78MB，速度快、准确率高）；
- 备：Vosk 中文小模型（~42MB），sherpa 不可用或无结果时回退。

模型文件：
- data/models/sherpa-paraformer/（model.onnx + tokens.txt）
- data/models/vosk-model-small-cn-0.22/

协同开发约定：仅由 app/api/digital_teacher.py 的 /asr 路由调用。
"""
from __future__ import annotations

import io
import json
import logging
import wave
from pathlib import Path

logger = logging.getLogger("agent.speech")

DATA_MODELS = Path(__file__).resolve().parents[2] / "data" / "models"
SHERPA_DIR = DATA_MODELS / "sherpa-paraformer"
SHERPA_MODEL = SHERPA_DIR / "model.onnx"
SHERPA_TOKENS = SHERPA_DIR / "tokens.txt"
VOSK_DIR = DATA_MODELS / "vosk-model-small-cn-0.22"


class SpeechRecognizer:
    """离线中文语音识别：sherpa-onnx Paraformer 优先，Vosk 后备"""

    def __init__(self) -> None:
        self._sherpa = None
        self._vosk = None  # (model, rec)

    # ------------------------------------------------------------ 识别入口
    def recognize(self, audio_bytes: bytes) -> str:
        """识别 WAV（任意采样率/声道，16bit），返回中文文本"""
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                rate = wf.getframerate()
                ch = wf.getnchannels()
                width = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
        except (wave.Error, EOFError) as exc:
            raise ValueError(f"无法解析 WAV: {exc}") from exc
        if not frames:
            return ""
        if width != 2:
            raise ValueError(f"仅支持 16bit PCM，实际 {width * 8}bit")
        pcm = self._to_mono16k(frames, ch, rate)
        if not pcm:
            return ""
        pcm = self._normalize(pcm)
        # 主引擎：sherpa
        try:
            text = self._decode_sherpa(pcm)
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("sherpa 识别失败，回退 Vosk: %s", exc)
        # 后备引擎：Vosk
        return self._decode_vosk(pcm)

    # ------------------------------------------------------------ sherpa
    def _ensure_sherpa(self) -> None:
        if self._sherpa is not None:
            return
        try:
            import sherpa_onnx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("未安装 sherpa-onnx，请执行: pip install sherpa-onnx") from exc
        if not (SHERPA_MODEL.exists() and SHERPA_TOKENS.exists()):
            raise RuntimeError(f"Paraformer 模型不完整: {SHERPA_DIR}")
        logger.info("加载 sherpa-onnx Paraformer 模型")
        self._sherpa = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(SHERPA_MODEL),
            tokens=str(SHERPA_TOKENS),
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
        )

    def _decode_sherpa(self, pcm16: bytes) -> str:
        self._ensure_sherpa()
        import numpy as np  # noqa: PLC0415
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        stream = self._sherpa.create_stream()
        stream.accept_waveform(16000, samples)
        self._sherpa.decode_stream(stream)
        return (stream.result.text or "").strip()

    # ------------------------------------------------------------ vosk 后备
    def _ensure_vosk(self) -> None:
        if self._vosk is not None:
            return
        try:
            from vosk import KaldiRecognizer, Model  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("未安装 vosk，请执行: pip install vosk") from exc
        if not VOSK_DIR.exists():
            raise RuntimeError(f"Vosk 模型不存在: {VOSK_DIR}")
        logger.info("加载 Vosk 模型（后备引擎）")
        model = Model(str(VOSK_DIR))
        self._vosk = (model, KaldiRecognizer(model, 16000))

    def _decode_vosk(self, pcm16: bytes) -> str:
        self._ensure_vosk()
        _model, rec = self._vosk
        rec.Reset()
        rec.AcceptWaveform(pcm16)
        res = json.loads(rec.FinalResult())
        return (res.get("text") or "").strip()

    # ------------------------------------------------------------ 音频处理
    @staticmethod
    def _to_mono16k(pcm: bytes, channels: int, rate: int) -> bytes:
        """16bit PCM → 16kHz 单声道（线性混单 + 线性插值降采样，纯 Python）"""
        import array
        import sys
        samples = array.array("h")
        samples.frombytes(pcm)
        if sys.byteorder == "big":
            samples.byteswap()
        if channels > 1:
            n = len(samples) // channels
            mono = array.array("h", (0 for _ in range(n)))
            for c in range(channels):
                for i in range(n):
                    mono[i] += samples[i * channels + c] // channels
            samples = mono
        if rate == 16000:
            return samples.tobytes()
        if rate < 16000 or rate > 48000:
            raise ValueError(f"采样率 {rate}Hz 不支持（需 16000-48000）")
        step = rate / 16000.0
        n_out = int(len(samples) / step)
        out = array.array("h", (0 for _ in range(n_out)))
        for i in range(n_out):
            pos = i * step
            j = int(pos)
            frac = pos - j
            j2 = j + 1 if j + 1 < len(samples) else j
            out[i] = int(samples[j] * (1.0 - frac) + samples[j2] * frac)
        return out.tobytes()

    @staticmethod
    def _normalize(pcm16: bytes) -> bytes:
        """峰值归一化：录音偏小时放大到 ~0.7 峰值（限最大 12x 增益）"""
        import array
        import sys
        samples = array.array("h")
        samples.frombytes(pcm16)
        if sys.byteorder == "big":
            samples.byteswap()
        peak = max((abs(s) for s in samples), default=0)
        if peak > 0 and peak < 0.55 * 32767:
            gain = min(0.7 * 32767 / peak, 12.0)
            if abs(gain - 1.0) > 0.01:
                for i in range(len(samples)):
                    samples[i] = int(max(-32768, min(32767, samples[i] * gain)))
        return samples.tobytes()


_recognizer: SpeechRecognizer | None = None


def get_recognizer() -> SpeechRecognizer:
    """模块级单例（懒加载）"""
    global _recognizer
    if _recognizer is None:
        _recognizer = SpeechRecognizer()
    return _recognizer
