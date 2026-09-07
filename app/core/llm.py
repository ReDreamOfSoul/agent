# -*- coding: utf-8 -*-
"""大模型客户端

支持两种后端：
- openai：OpenAI 兼容 Chat Completions 接口（阿里云 DashScope 兼容模式 / OpenAI / 本地 vLLM 等）
- dummy ：本地模拟应答（无密钥联调、单元测试用，不用于真实教学服务）

对应设计文档「LLM整合生成 / 通用逻辑生成」节点。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger("agent.llm")

DUMMY_SYSTEM = (
    "你是《机器人学导论》课程的AI助教。回答要求：简洁直接，只回答学生问的问题，"
    "不要说'根据资料/依据如下/课程资料片段中'等套话，不要列出参考来源或引用片段编号，"
    "不要使用LaTeX公式语法（如\\[\\]、\\(\\)、\\text{}等），数学公式用普通文本表达（如'总评成绩 = 课堂表现×10% + 期末×70%'）。"
)


class LLMError(Exception):
    """大模型调用异常"""


class BaseLLM:
    def chat(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024) -> str:
        raise NotImplementedError

    def generate_with_kb(self, question: str, context: list[str],
                         system: str | None = None,
                         history: list[dict] | None = None) -> str:
        """基于知识库片段的整合生成（history 为最近多轮对话，供解析指代）"""
        system = system or DUMMY_SYSTEM
        context_text = "\n\n".join(f"[片段{i+1}] {c}" for i, c in enumerate(context))
        messages = [{"role": "system", "content": system}] + list(history or []) + [
            {"role": "user", "content": (
                f"课程资料片段如下（仅作参考依据，请忠实使用其中内容，不要编造课程资料中不存在的信息）：\n"
                f"{context_text}\n\n学生问题：{question}\n\n"
                "请简洁直接地回答学生问题，不要说'根据资料/依据如下'等套话，不要列出参考来源，不要使用LaTeX公式语法，数学公式用普通文本表达。"
            )},
        ]
        return self.chat(messages)

    def generate_generic(self, question: str, system: str | None = None,
                         history: list[dict] | None = None) -> str:
        """知识库无匹配时的课程语境兜底生成（history 为最近多轮对话，供解析指代）"""
        system = system or DUMMY_SYSTEM
        messages = [{"role": "system", "content": system}] + list(history or []) + [
            {"role": "user", "content": (
                f"学生问题：{question}\n\n"
                "该问题在课程知识库中没有找到直接对应的内容。请基于机器人学通用学科逻辑与课程知识框架，"
                "简洁直接地给出贴合课程语境的参考答案；若超出课程范围，请给出学习引导建议。不要说套话，不要使用LaTeX公式语法。"
            )},
        ]
        return self.chat(messages)

    def generate_followup(self, question: str, answer: str, keywords: list[str]) -> str:
        """引导式追问：回答后反向提问，探测背景知识掌握程度"""
        kw = "、".join(keywords[:3]) if keywords else "本知识点"
        messages = [
            {"role": "system", "content": DUMMY_SYSTEM},
            {"role": "user", "content": (
                f"学生刚才提问：{question}\n你的回答：{answer}\n\n"
                f"请基于「{kw}」的前置知识关系，向学生提出一个由浅入深、用于探测其背景知识掌握程度的追问问题，"
                "只输出问题本身，不要输出其他内容。"
            )},
        ]
        return self.chat(messages, temperature=0.5, max_tokens=200)

    def subjective_feedback(self, question: str, answer: str, score: float) -> str:
        """无参考答案时生成主观题诊断反馈（教师复核参考，不评分）"""
        messages = [
            {"role": "system", "content": DUMMY_SYSTEM},
            {"role": "user", "content": (
                f"题目：{question}\n学生作答：{answer}\n\n"
                "题库中未找到该题参考答案，无法精确判分。请以课程助教身份给出诊断性反馈："
                "指出作答中体现的合理要点与明显欠缺，并给出复习建议，150字以内，不要给出分数。"
            )},
        ]
        return self.chat(messages, temperature=0.3, max_tokens=300)

    def generate_exercise(self, context: str, chapter: str = "") -> dict | None:
        """基于课程资料片段生成一道自测题（简答题 + 参考答案），返回 {"question","answer"}"""
        messages = [
            {"role": "system", "content": DUMMY_SYSTEM},
            {"role": "user", "content": (
                f"请基于以下课程资料片段出一道适合本科生的自测简答题，并给出参考答案。\n"
                f"所属章节：{chapter or '未标注'}\n"
                f"资料片段：\n{context[:800]}\n\n"
                "严格只输出 JSON，不要输出其他内容，格式："
                '{"question": "题目", "answer": "参考答案（2-4句，要点明确）"}'
            )},
        ]
        text = self.chat(messages, temperature=0.4, max_tokens=400)
        return _parse_exercise_json(text)


def _parse_exercise_json(text: str) -> dict | None:
    """从 LLM 输出中解析 {"question":..., "answer":...}（容忍 ```json 包裹等噪声）"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    q = str(data.get("question", "")).strip()
    a = str(data.get("answer", "")).strip()
    if len(q) < 5 or len(a) < 3:
        return None
    return {"question": q, "answer": a}


class OpenAICompatibleLLM(BaseLLM):
    """OpenAI 兼容 Chat Completions"""

    def __init__(self, api_base_url: str, api_key: str, model: str,
                 timeout: int = 60, retry_times: int = 1):
        if not api_base_url or not api_key or not model:
            raise LLMError("LLM 配置不完整：请配置 api_base_url / api_key / api_model")
        self.base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retry_times = retry_times

    def chat(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_err: Exception | None = None
        for attempt in range(self.retry_times + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code != 200:
                    raise LLMError(f"LLM 接口返回 {resp.status_code}: {resp.text[:200]}")
                content = resp.json()["choices"][0]["message"]["content"]
                return content.strip()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("LLM 调用第 %d 次失败: %s", attempt + 1, exc)
                if attempt < self.retry_times:
                    time.sleep(1.0)
        raise LLMError(f"LLM 调用失败: {last_err}")


class DummyLLM(BaseLLM):
    """本地模拟应答：保证无密钥时全链路可运行、可测试"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024) -> str:
        # 取最后一条 user 消息，提取学生问题（"学生问题：..."之后）
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        text = user_msgs[-1] if user_msgs else ""
        question = text.split("学生问题：", 1)[-1].split("\n", 1)[0].strip()
        self.calls.append(question)
        if "片段" in text and "课程资料片段" in text:
            # 知识库命中场景：抽取课程片段前文并组织为结构化模拟回答
            ctx = text.split("课程资料片段如下", 1)[-1].split("学生问题：", 1)[0]
            excerpt = " ".join(ctx.split())[:90]
            return (
                "（dummy 后端模拟知识库应答，依据课程资料片段）\n"
                f"1. 关于「{question}」：结合课程资料，{excerpt}……\n"
                "2. 学习要点：建议结合教材对应章节与课件进一步理解概念与公式推导。\n"
                "3. 练习建议：完成题库中相关习题，检验掌握程度。"
            )
        return (
            "（dummy 后端模拟通用应答）\n"
            f"关于「{question}」：该问题在课程知识库中未检索到直接对应内容。\n"
            "建议结合《机器人学导论》课程大纲明确学习目标，并向任课教师反馈以补充课程资料。"
        )

    def subjective_feedback(self, question: str, answer: str, score: float) -> str:
        self.calls.append(question)
        return (f"（dummy 后端模拟诊断反馈）作答约 {len(answer)} 字，题库无参考答案可比对，"
                "无法自动评估要点覆盖，建议任课教师人工复核。")

    def generate_exercise(self, context: str, chapter: str = "") -> dict | None:
        self.calls.append(f"出题:{chapter}")
        topic = chapter or "课程知识点"
        return {
            "question": f"请结合「{topic}」的学习内容，简述其核心概念与典型应用。",
            "answer": "（dummy 模拟参考答案）请围绕资料片段中的定义、分类与典型应用场景，分点作答。",
        }


def create_llm(cfg) -> BaseLLM:
    """按配置创建 LLM 客户端"""
    llm_cfg = cfg.get("llm", {}) or {}
    provider = llm_cfg.get("provider", "dummy")
    if provider == "openai":
        return OpenAICompatibleLLM(
            api_base_url=llm_cfg.get("api_base_url", ""),
            api_key=llm_cfg.get("api_key", ""),
            model=llm_cfg.get("api_model", ""),
            timeout=llm_cfg.get("timeout_seconds", 60),
            retry_times=llm_cfg.get("retry_times", 1),
        )
    if provider == "dummy":
        return DummyLLM()
    raise LLMError(f"未知的 LLM provider: {provider}")
