# -*- coding: utf-8 -*-
"""数字教师（知识库研讨）核心引擎 —— 独立新增模块

功能：
- 多位拥有不同专业人设的 AI 教师，基于课程知识库检索片段就同一主题展开研讨式对话；
- 用户可随时插入自己的提问，教师团队基于知识库继续回应。

协同开发约定（两人不同电脑协作）：
- 本模块只复用 app.state 上的公共对象 kb（KnowledgeBase）与 llm（BaseLLM），
  不修改 workflow / llm / chat 等任何既有文件；
- 对外仅由 app/api/digital_teacher.py 调用；路由注册见 app/main.py（两行）。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("agent.digital_teacher")

# ---------------------------------------------------------------- 教师角色配置
TEACHERS: list[dict] = [
    {
        "id": "kinematics",
        "name": "陈教授",
        "title": "机器人运动学",
        "icon": "🤖",
        "expertise": "位姿描述、齐次变换矩阵、D-H 参数法、正/逆运动学、雅可比矩阵、速度与静力分析",
        "keywords": ["运动学", "齐次变换", "D-H", "雅可比", "位姿", "正解", "逆解", "坐标系"],
    },
    {
        "id": "dynamics",
        "name": "李教授",
        "title": "机器人动力学",
        "icon": "⚙️",
        "expertise": "牛顿-欧拉法、拉格朗日法、惯性矩阵、动力学方程、动力学性能分析与仿真",
        "keywords": ["动力学", "拉格朗日", "牛顿", "惯性", "力矩", "动力学方程", "动态"],
    },
    {
        "id": "trajectory",
        "name": "王教授",
        "title": "轨迹规划",
        "icon": "📈",
        "expertise": "关节空间与笛卡尔空间轨迹规划、多项式插值、速度/加速度约束、时间最优轨迹",
        "keywords": ["轨迹", "规划", "插值", "关节空间", "笛卡尔", "路径", "平滑"],
    },
    {
        "id": "control",
        "name": "赵教授",
        "title": "机器人控制",
        "icon": "🎛️",
        "expertise": "PID 控制、伺服系统、力/位混合控制、阻抗控制、控制系统稳定性与校正",
        "keywords": ["控制", "PID", "伺服", "稳定", "反馈", "位置控制", "力控制", "校正"],
    },
    {
        "id": "host",
        "name": "主持人",
        "title": "课程总教师",
        "icon": "🧑‍🏫",
        "expertise": "《机器人学导论》课程整体框架：教学大纲、章节衔接、概念辨析、学习建议",
        "keywords": ["课程", "大纲", "总结", "衔接", "建议", "考试", "重点"],
    },
]

# 每轮默认参与研讨的专业教师数（不含主持人）
ROUND_SPEAKERS = 2

# 检索参数：研讨场景需充分引用知识库，阈值放宽
RETRIEVE_TOP_K = 6
RETRIEVE_THRESHOLD = 0.20

_SYSTEM_BASE = (
    "你是《机器人学导论》课程的{title}「{name}」，正在参加一场基于课程知识库的教师研讨会。"
    "请始终依据给出的【课程资料片段】发言，只讨论与课程相关的内容；"
    "回答简洁、专业、有教学视角，不使用 LaTeX 公式语法，数学公式用普通文本表达，"
    "不要说'根据资料/依据片段'等套话，直接给出观点。"
)


@dataclass
class DtMessage:
    """研讨消息：用户发言 / 教师发言 / 系统提示"""
    type: str                # user | teacher | system
    role: str                # user | teacher 角色名（teacher 时为教师名）
    teacher_id: str = ""     # 发言教师 id（type=teacher 时有值）
    title: str = ""          # 教师专业标签
    icon: str = ""           # 教师头像
    content: str = ""
    round_no: int = 0
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "role": self.role,
            "teacher_id": self.teacher_id,
            "title": self.title,
            "icon": self.icon,
            "content": self.content,
            "round_no": self.round_no,
            "ts": round(self.ts, 3),
        }


@dataclass
class DtSession:
    session_id: str
    topic: str
    round_no: int = 0
    messages: list[DtMessage] = field(default_factory=list)
    last_kb_text: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "round_no": self.round_no,
            "messages": [m.to_dict() for m in self.messages],
        }


class DigitalTeacherEngine:
    """研讨引擎：持有知识库与 LLM，管理多个研讨会话（内存态）"""

    def __init__(self, kb, llm):
        self.kb = kb
        self.llm = llm
        self._sessions: dict[str, DtSession] = {}
        self._lock = threading.Lock()
        logger.info("数字教师引擎就绪（%d 位教师）", len(TEACHERS))

    # ------------------------------------------------------------ 对外接口
    def list_teachers(self) -> list[dict]:
        return [
            {"id": t["id"], "name": t["name"], "title": t["title"],
             "icon": t["icon"], "expertise": t["expertise"]}
            for t in TEACHERS
        ]

    def start(self, topic: str, rounds: int = 1) -> dict:
        """以主题开启一轮研讨：检索知识库 → 相关教师发言 → 主持人总结"""
        if not topic or not topic.strip():
            raise ValueError("研讨主题不能为空")
        topic = topic.strip()
        session = DtSession(session_id=uuid.uuid4().hex[:12], topic=topic)
        session.messages.append(DtMessage(type="system", role="system",
                                          content=f"研讨主题：{topic}（已检索课程知识库）"))
        self._retrieve(session, topic)
        with self._lock:
            self._sessions[session.session_id] = session
        self._run_round(session, user_question=topic, rounds=max(1, int(rounds)))
        return session.to_dict()

    def join(self, session_id: str, message: str) -> dict:
        """用户插入发言，教师团队回应一轮"""
        if not message or not message.strip():
            raise ValueError("发言内容不能为空")
        session = self._get(session_id)
        session.round_no += 1
        session.messages.append(DtMessage(
            type="user", role="用户", content=message.strip(),
            round_no=session.round_no,
        ))
        self._retrieve(session, message)
        self._run_round(session, user_question=message)
        return session.to_dict()

    def get(self, session_id: str) -> dict:
        return self._get(session_id).to_dict()

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()

    # ------------------------------------------------------------ 内部实现
    def _get(self, session_id: str) -> DtSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"研讨会话不存在或已过期: {session_id}")
        return session

    def _retrieve(self, session: DtSession, query: str) -> None:
        """检索知识库并缓存文本片段（供本轮所有教师引用）"""
        try:
            from ..rag.knowledge_base import chunks_to_text
            ret = self.kb.retrieve(
                query, top_k=RETRIEVE_TOP_K, threshold=RETRIEVE_THRESHOLD,
                threshold_keyword=RETRIEVE_THRESHOLD,
            )
            session.last_kb_text = chunks_to_text(ret.get("results") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("研讨检索失败(%s): %s", query[:20], exc)
            session.last_kb_text = []

    def _pick_speakers(self, session: DtSession, topic: str, count: int = ROUND_SPEAKERS) -> list[dict]:
        """按主题关键词匹配最相关的 count 位专业教师（不含主持人）"""
        pros = [t for t in TEACHERS if t["id"] != "host"]
        scored = []
        for t in pros:
            s = sum(1 for kw in t["keywords"] if kw in topic)
            # 简单加分：主题含其专长标题词
            if t["title"] in topic:
                s += 2
            scored.append((s, t))
        scored.sort(key=lambda x: -x[0])
        picked = [t for _, t in scored if _ > 0]
        # 相关度不足时按固定轮转补齐，保证研讨有来有往
        if len(picked) < count:
            order = [t["id"] for t in pros]
            i = session.round_no % len(order)
            for t in pros:
                if len(picked) >= count:
                    break
                if t not in picked:
                    picked.append(t)
        return picked[:count]

    def _speak(self, session: DtSession, teacher: dict,
               user_question: str, peer_lines: list[str]) -> str:
        """让一位教师基于知识库片段与前面发言生成一段话"""
        context = session.last_kb_text or ["（本次检索未获得课程片段，请基于课程通用知识简要回答）"]
        ctx_text = "\n\n".join(f"[片段{i+1}] {c}" for i, c in enumerate(context))
        peer = "\n".join(peer_lines) if peer_lines else "（本研讨第一轮，尚无其他教师发言）"
        system = _SYSTEM_BASE.format(title=teacher["title"], name=teacher["name"])
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"【课程资料片段】\n{ctx_text}\n\n"
                f"【研讨主题】{session.topic}\n"
                f"【本次要讨论的问题】{user_question}\n\n"
                f"【前面其他教师的发言】\n{peer}\n\n"
                f"请你以{teacher['title']}的视角发言：紧扣问题与课程资料，"
                "如有不同观点请礼貌指出并给出依据；篇幅 150-260 字，不要复述他人发言。"
            )},
        ]
        try:
            return self.llm.chat(messages, temperature=0.3, max_tokens=800).strip()
        except Exception as exc:  # noqa: BLE001
            logger.exception("数字教师发言失败(%s)", teacher["id"])
            return f"（{teacher['name']}暂时无法发言：{exc}）"

    def _summarize(self, session: DtSession, speeches: list[tuple[dict, str]],
                   user_question: str) -> str:
        """主持人总结本轮研讨"""
        host = next(t for t in TEACHERS if t["id"] == "host")
        context = session.last_kb_text or []
        ctx_text = "\n\n".join(f"[片段{i+1}] {c}" for i, c in enumerate(context[:4]))
        lines = "\n".join(f"- {t['name']}（{t['title']}）：{txt[:120]}" for t, txt in speeches)
        system = _SYSTEM_BASE.format(title=host["title"], name=host["name"])
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"【课程资料片段】\n{ctx_text}\n\n"
                f"【研讨主题】{session.topic}\n【本轮讨论的问题】{user_question}\n\n"
                f"【本轮教师发言摘要】\n{lines}\n\n"
                "请以主持人身份做 120-200 字的小结：提炼共识、点出分歧或补充点，"
                "并给学生一条可操作的学习建议。不要复述整段发言。"
            )},
        ]
        try:
            return self.llm.chat(messages, temperature=0.3, max_tokens=600).strip()
        except Exception as exc:  # noqa: BLE001
            logger.exception("数字教师总结失败")
            return f"（主持人小结生成失败：{exc}）"

    def _run_round(self, session: DtSession, user_question: str, rounds: int = 1) -> None:
        """执行一轮（或多轮）研讨：每位选中的教师发言 → 主持人总结"""
        for _ in range(max(1, rounds)):
            session.round_no += 1
            rn = session.round_no
            speakers = self._pick_speakers(session, user_question)
            speeches: list[tuple[dict, str]] = []
            peer_lines: list[str] = []
            for teacher in speakers:
                text = self._speak(session, teacher, user_question, peer_lines)
                session.messages.append(DtMessage(
                    type="teacher", role=teacher["name"],
                    teacher_id=teacher["id"], title=teacher["title"],
                    icon=teacher["icon"], content=text, round_no=rn,
                ))
                speeches.append((teacher, text))
                peer_lines.append(f"{teacher['name']}（{teacher['title']}）：{text}")
            summary = self._summarize(session, speeches, user_question)
            session.messages.append(DtMessage(
                type="teacher", role="主持人",
                teacher_id="host", title="课程总教师", icon="🧑‍🏫",
                content=summary, round_no=rn,
            ))


def create_engine(kb, llm) -> DigitalTeacherEngine:
    """工厂函数：供 API 模块懒加载使用"""
    return DigitalTeacherEngine(kb, llm)


# ================================================================
# 一对一数字教师（AI 形象 + 语音互动，独立新增，与研讨引擎共存）
# ================================================================

# 单教师人设（AI 形象对应女性教师）
TUTOR: dict = {
    "id": "tutor",
    "name": "林老师",
    "title": "《机器人学导论》课程数字教师",
    "icon": "🧑‍🏫",
}

_TUTOR_SYSTEM = (
    "你是《机器人学导论》课程数字教师林老师。请依据【课程资料片段】回答学生提问。"
    "回答将被语音朗读，必须：口语化短句、无 Markdown/LaTeX/特殊符号，公式用自然语言"
    "（如“四乘四的齐次变换矩阵”）；篇幅 80-140 字；不说“根据资料”等套话，不要表情符号。"
)

# 会话历史保留轮数（user+assistant 各计一条）
TUTOR_HISTORY_LIMIT = 4
# 内存中最多保存的会话数（超出后淘汰最早会话）
TUTOR_SESSION_LIMIT = 30

# 检索参数：与研讨引擎一致
TUTOR_RETRIEVE_TOP_K = 3
TUTOR_RETRIEVE_THRESHOLD = 0.20

# 欢迎语（前端本地语音播报用，不消耗 LLM）
TUTOR_WELCOME = (
    "同学们好，我是《机器人学导论》课程的数字教师林老师。"
    "你可以打字提问，也可以让我为你讲解任意课程知识点，我会结合课程知识库为你答疑解惑。"
)


class DigitalTutorEngine:
    """一对一数字教师引擎：知识库检索 + LLM 讲解 + 内存多轮会话（供前端 AI 形象对话/朗读）"""

    def __init__(self, kb, llm):
        self.kb = kb
        self.llm = llm
        # session_id -> [{"role": "user"|"assistant", "content": str}]
        self._sessions: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        logger.info("数字教师（一对一）就绪：%s", TUTOR["name"])

    # ------------------------------------------------------------ 对外接口
    def talk(self, message: str, session_id: str | None = None) -> dict:
        """学生提问 → 检索知识库 → 林老师讲解（带多轮上下文）"""
        message = (message or "").strip()
        if not message:
            raise ValueError("提问内容不能为空")
        with self._lock:
            if session_id is not None and session_id not in self._sessions:
                raise KeyError(f"会话不存在或已过期: {session_id}")
            sid = session_id or self._new_session_locked()
            hist = list(self._sessions[sid])

        messages = self._build_messages(message, hist)
        reply = self.llm.chat(messages, temperature=0.3, max_tokens=350).strip()

        with self._lock:
            hist2 = self._sessions.get(sid, [])
            hist2.append({"role": "user", "content": message})
            hist2.append({"role": "assistant", "content": reply})
            if len(hist2) > TUTOR_HISTORY_LIMIT * 2:
                self._sessions[sid] = hist2[-TUTOR_HISTORY_LIMIT * 2:]
        return {"session_id": sid, "teacher": dict(TUTOR), "reply": reply}

    def _build_messages(self, message: str, hist: list[dict]) -> list[dict]:
        """组装 LLM 消息（系统人设 + 多轮历史 + 知识片段 + 提问）"""
        ctx = self._retrieve_text(message)
        if ctx:
            ctx_text = "\n\n".join(f"[片段{i+1}] {c}" for i, c in enumerate(ctx))
        else:
            ctx_text = "（本次未检索到课程片段，请基于课程通用知识简要回答）"
        messages = [{"role": "system", "content": _TUTOR_SYSTEM}]
        messages.extend(hist[-TUTOR_HISTORY_LIMIT * 2:])
        messages.append({"role": "user", "content": (
            f"【课程资料片段】\n{ctx_text}\n\n【学生提问】{message}\n\n"
            "请以林老师的口吻讲解，口语化、可直接朗读。"
        )})
        return messages

    def talk_stream(self, message: str, session_id: str | None = None):
        """流式对话：检索知识库 → 组装 prompt → 直连 LLM API 边生成边产出句子。

        生成器逐句 yield 回答文本；消费完毕后 return 会话 id。
        直连 OpenAI 兼容接口实现流式（不修改既有 llm.py）；失败/非 openai 后端
        自动回退非流式 llm.chat。
        """
        message = (message or "").strip()
        if not message:
            raise ValueError("提问内容不能为空")
        with self._lock:
            if session_id is not None and session_id not in self._sessions:
                raise KeyError(f"会话不存在或已过期: {session_id}")
            sid = session_id or self._new_session_locked()
            hist = list(self._sessions[sid])
        messages = self._build_messages(message, hist)
        full = ""
        for piece in self._llm_stream(messages):
            full += piece
            yield piece
        full = full.strip()
        if not full:
            full = self.llm.chat(messages, temperature=0.3, max_tokens=350).strip()
            if full:
                yield full
        with self._lock:
            hist2 = self._sessions.get(sid, [])
            hist2.append({"role": "user", "content": message})
            hist2.append({"role": "assistant", "content": full})
            if len(hist2) > TUTOR_HISTORY_LIMIT * 2:
                self._sessions[sid] = hist2[-TUTOR_HISTORY_LIMIT * 2:]
        return sid

    def _llm_stream(self, messages: list[dict]):
        """直连 OpenAI 兼容 API 的流式生成（SSE），按句产出；异常时回退非流式"""
        try:
            from ..core.config import load_config  # noqa: PLC0415
            import json  # noqa: PLC0415
            import re  # noqa: PLC0415
            import requests  # noqa: PLC0415
            llm_cfg = load_config().get("llm") or {}
            if llm_cfg.get("provider") != "openai" or not llm_cfg.get("api_base_url"):
                text = self.llm.chat(messages, temperature=0.3, max_tokens=350).strip()
                if text:
                    yield text
                return
            url = llm_cfg["api_base_url"].rstrip("/") + "/chat/completions"
            payload = {
                "model": llm_cfg["api_model"],
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 350,
                "stream": True,
                "enable_thinking": False,
            }
            headers = {"Authorization": f"Bearer {llm_cfg['api_key']}",
                       "Content-Type": "application/json"}
            with requests.post(url, json=payload, headers=headers,
                               stream=True, timeout=120) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"LLM 接口返回 {resp.status_code}")
                buf = ""
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if not delta:
                        continue
                    buf += delta
                    while True:
                        m = re.search(r"[。！？；!?;\n]", buf)
                        if not m:
                            break
                        idx = m.end()
                        sent = buf[:idx].strip()
                        buf = buf[idx:]
                        if sent:
                            yield sent
                rest = buf.strip()
                if rest:
                    yield rest
        except Exception as exc:  # noqa: BLE001
            logger.warning("流式生成失败，回退非流式: %s", exc)
            text = self.llm.chat(messages, temperature=0.3, max_tokens=350).strip()
            if text:
                yield text

    def welcome(self) -> str:
        return TUTOR_WELCOME

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()

    # ------------------------------------------------------------ 内部实现
    def _new_session_locked(self) -> str:
        sid = uuid.uuid4().hex[:12]
        self._sessions[sid] = []
        while len(self._sessions) > TUTOR_SESSION_LIMIT:
            self._sessions.pop(next(iter(self._sessions)))
        return sid

    def _retrieve_text(self, query: str) -> list[str]:
        try:
            from ..rag.knowledge_base import chunks_to_text
            ret = self.kb.retrieve(
                query, top_k=TUTOR_RETRIEVE_TOP_K, threshold=TUTOR_RETRIEVE_THRESHOLD,
                threshold_keyword=TUTOR_RETRIEVE_THRESHOLD,
            )
            return chunks_to_text(ret.get("results") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("数字教师检索失败(%s): %s", query[:20], exc)
            return []


def create_tutor_engine(kb, llm) -> DigitalTutorEngine:
    """工厂函数：供 API 模块懒加载使用"""
    return DigitalTutorEngine(kb, llm)
