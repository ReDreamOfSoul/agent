# 《机器人学导论》课程智能体 —— 项目代码

成都理工大学2025年度“AI研究基金”项目（**2025AI068**，AI+人才培养）
**AI赋能机器人学课程的教学改革和实践探索** —— 课程智能体开发代码。

对应《机器人学导论课程智能体设计方案》的工程实现，作为项目结题材料中的**项目代码**部分提交。

---

## 1. 项目概述

本代码实现《机器人学导论》课程AI助教智能体的完整链路：

```
开始 → 意图识别 → 知识库检索（向量+BM25混合）→ 判断RAG输出
   →（命中）LLM整合生成 /（未命中）通用逻辑生成 → 文本处理 → 回复 → 交互记录（学情库）
```

核心能力：
- **知识库**：解析教材PDF、十章课件（PPT/PPTX）、题库、教学大纲（DOC/DOCX），标题感知分段、重叠分块、章节打标；
- **检索**：BM25关键词检索 + 可选向量检索（OpenAI兼容/本地Embedding）混合检索、阈值命中判定、归一化重排；
- **生成**：基于课程资料整合生成（RAG）/ 课程语境兜底生成 / 引导式追问；
- **作业批阅**：题库匹配 → 客观题精确判定、主观题要点评分 → 反馈推送；
- **学情分析**：SQLite 记录交互/作业/反馈数据，教师端学情汇总（活跃度、命中率、薄弱点、反馈评分）；
- **视频课程**：知识库 `video/` 目录登记教学视频清单，检索命中相关章节自动推荐配套视频，学生端提供视频课程面板在线播放；
- **MCP协议**：course_kb_retrieval / exercise_judge / study_record / graph_query 四个工具按统一MCP协议注册，提供 JSON-RPC over stdio 服务端；
- **服务接口**：FastAPI 提供 /v1/chat（含 SSE 流式）、/v1/sessions、/v1/homework/judge、/v1/feedback、/v1/insight、/v1/graph、/v1/exercises、/v1/recommend、/v1/self-assessment、/v1/export、/v1/videos。

## 2. 目录结构

```
智能体开发代码/
├── README.md                  # 本文件
├── requirements.txt           # 依赖清单
├── .env.example               # 环境变量示例（复制为 .env 填写）
├── config/
│   └── config.yaml            # 主配置（检索参数/模型/知识库路径）
├── app/
│   ├── main.py                # FastAPI 应用入口（含 /videos 静态服务）
│   ├── api/                   # 接口层（chat/sessions/homework/feedback/insight/graph/exercise/student/admin/videos）
│   ├── core/                  # 工作流引擎/LLM/会话/学情/文本处理/作业批阅/知识图谱
│   ├── rag/                   # 知识库/BM25/Embedding/文档解析器
│   └── mcp/                   # MCP 工具注册与 JSON-RPC 服务端
├── scripts/
│   ├── build_kb.py            # 知识库索引构建
│   ├── gen_video_manifest.py  # 扫描知识库 video/ 生成视频清单（可人工补充标题/描述）
│   └── test_agent.py          # 命令行联调
├── tests/
│   ├── test_workflow.py       # 工作流单元测试（无需真实数据/模型）
│   └── test_agent_modules.py  # 习题解析/知识图谱/学情画像/导出 单元测试
└── data/
    ├── knowledge_base/        # 知识库源文件（教材/课件/题库/大纲/video，放入此处）
    ├── index/                 # 构建后的索引（chunks.json + bm25.pkl [+ vectors.npz]）
    └── study.db               # 学情数据库（运行后自动生成）
```

## 3. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows / Linux |
| Python | 3.10 及以上（本地Embedding模型建议 Python 3.10 环境） |
| 依赖 | 见 requirements.txt |

## 4. 安装与运行

### 4.1 安装依赖

```bash
pip install -r requirements.txt
```

### 4.2 准备知识库

将课程资料放入 `data/knowledge_base/`（支持 `.pdf .ppt .pptx .doc .docx`），例如：

```
data/knowledge_base/
├── 机器人技术基础 第3版.pdf      # 教材
├── 第一章 绪论.pptx              # 课件（第一~十章）
├── 机器人学导论-题库.doc         # 题库
├── 机器人学导论 课程教学大纲.doc # 教学大纲
└── video/                       # 教学视频（mp4/webm…，可选）
    ├── ch06_1.mp4               # 建议命名 ch{章节}_{序号}.mp4，自动识别所属章节
    └── manifest.json            # 视频清单（python scripts/gen_video_manifest.py 生成，可人工编辑标题/描述/时长）
```

视频接入说明：
1. 将视频放入 `data/knowledge_base/video/`，运行 `python scripts/gen_video_manifest.py` 自动生成/更新 `manifest.json`（文件名 `ch{章}_{序号}` 自动推断所属章节）；
2. 如需补充简介或外部链接，直接编辑 `manifest.json`（`description` 字段；`video_url` 也支持填写完整 http 链接）；
3. 重建索引后视频即成为可检索的知识块：`python scripts/build_kb.py --rebuild`；
4. 前端「视频课程」面板按章节展示并在线播放；智能问答命中某章节知识点时自动推荐该章节配套视频。

### 4.3 构建知识库索引

```bash
python scripts/build_kb.py            # 首次构建
python scripts/build_kb.py --rebuild  # 强制重建
python scripts/build_kb.py --no-ocr   # 跳过扫描版PDF的OCR（快速试跑）
```

> 扫描版教材（无文字层 PDF）自动启用 OCR 识别（RapidOCR），识别结果缓存在
> `data/cache/`，同一 PDF 只需识别一次，之后构建直接复用缓存。
> 未配置向量服务时，自动使用 **BM25 关键词检索** 模式，功能完整可用。

### 4.4 配置模型服务（可选，推荐）

复制 `.env.example` 为 `.env` 并填写：

```ini
# 对话大模型（OpenAI 兼容，例如阿里云 DashScope 兼容模式）
LLM_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-xxxx
LLM_MODEL=qwen-plus

# Embedding 向量模型（配置后自动启用"向量+BM25"混合检索）
EMBEDDING_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=sk-xxxx
EMBEDDING_MODEL=text-embedding-v3
```

未配置时使用 **dummy 后端**（本地模拟应答），保证离线联调与测试可运行。

本地 Embedding（可选，需 Python 3.10 环境）：
```bash
pip install sentence-transformers
# 修改 config/config.yaml → embedding.provider: local（默认模型 BAAI/bge-small-zh-v1.5）
# 模型文件随 sentence-transformers 自动下载至 ~/.cache/huggingface，可作为"模型文件"成果备份
```

### 4.5 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 或
python -m uvicorn app.main:app --port 8000
```

接口文档：http://localhost:8000/docs

### 4.6 命令行联调

```bash
python scripts/test_agent.py "请介绍一种机器视觉滤波去噪算法"   # 单次问答
python scripts/test_agent.py --chat                            # 交互式问答
python scripts/test_agent.py --homework "什么是齐次变换矩阵" "我的答案"  # 作业批阅
```

### 4.7 单元测试

```bash
python tests/test_workflow.py
# 或
python -m pytest tests/ -v
```

## 5. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /health | 健康检查 |
| POST | /v1/chat | 对话：`{"session_id":"可选","question":"问题"}` |
| POST | /v1/chat/stream | 对话（SSE 流式）：同 /v1/chat，事件 meta→delta→done，前端打字机渲染 |
| POST | /v1/sessions | 创建会话 |
| DELETE | /v1/sessions/{id} | 结束会话 |
| POST | /v1/homework/judge | 作业批阅：`{"question_text":"题目","student_answer":"作答"}` |
| POST | /v1/feedback | 答案反馈：`{"question":"问题","rating":1-5,"comment":"可选"}` |
| GET | /v1/insight?days=30 | 学情汇总（教师端） |
| GET | /v1/graph/overview | 课程知识图谱总览（章节前置/后继/学习目标） |
| GET | /v1/graph?node=…&relation_type=… | 知识点关系查询（prerequisite/successor/objective） |
| GET | /v1/exercises?limit=5 | 习题自测抽题（题库优先，不足时 AI 生成） |
| GET | /v1/self-assessment?session_id=… | 学生个人学习画像（命中率/薄弱点/待订正） |
| GET | /v1/recommend?session_id=… | 个性化学习推荐（知识补强/习题订正/学习建议） |
| GET | /v1/videos | 视频课程清单（按章节分组，含播放地址） |
| GET | /videos/{file} | 视频文件静态访问（知识库 video/ 目录） |
| GET | /v1/course/meta | 课程元信息（课程名称/教师/教材/学分/考核/先修课程/教学大纲十章等） |
| GET | /v1/export/interactions?days=30 | 导出交互记录 CSV（homework/feedback 同理） |
| GET | /v1/tools | MCP 工具清单 |

## 6. MCP 协议对接

MCP 服务端提供 4 个工具（`course_kb_retrieval` / `exercise_judge` / `study_record` / `graph_query`），
遵循统一MCP协议（JSON-RPC 2.0 over stdio）：

```bash
python -m app.mcp.server
```

与学校平台的对接方式（二选一）：
1. **MCP工具方式**：将本服务作为 MCP Server 接入学校智能体开发平台，平台流程编排节点通过 `tools/call` 调用上述工具；
2. **HTTP接口方式**：平台直接调用 5.1 节 REST 接口完成对话与批阅。

工具 Schema 定义见 `app/mcp/tools.py`，可通过 `GET /v1/tools` 在线查看。

## 7. 关键参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| 检索 top_k | 5 | 候选片段数量 |
| 命中阈值 θ | 0.72 | 向量/混合模式：最高归一化得分 ≥ θ 判定为知识库命中（初始设计值，需实测调优） |
| 关键词阈值 | 0.5 | 纯关键词（未配置Embedding）模式的命中阈值（idf加权覆盖率语义） |
| 混合权重 | 向量0.6 / 关键词0.4 | 配置向量服务后生效 |
| 分块大小/重叠 | 500 / 60 字符 | 标题感知分段 + 重叠分块 |
| 扫描版OCR | 自动启用 | 无文字层PDF页经 RapidOCR 识别（结果缓存于 data/cache） |
| 会话上下文 | 最近10轮 / 30分钟超时 | 多轮对话管理 |
| 生成温度 | 0.3 | 保证回答稳定准确 |

以上参数在 `config/config.yaml` 中集中配置。

## 8. 成果对应关系

| 结题材料 | 对应本代码 |
|---|---|
| 项目代码 | 本目录全部代码 |
| 模型文件 | 本地Embedding模型文件（sentence-transformers 缓存，可选）；配置见 embedding.provider=local |
| 相关成果材料 | 《机器人学导论课程智能体设计方案》文档 + 本代码 |

## 9. 说明与限制

1. **知识库格式限制**：学校智能体平台知识库不支持 PPT/视频上传，本代码采用"本地离线解析+分段后入库"方式规避（`app/rag/parsers/`）；视频仅登记清单（标题/章节/地址），本地视频经 `/videos` 静态服务播放，也可在 manifest.json 中改用在线链接；
2. **.doc 老格式**：题库、教学大纲为 .doc 格式，Windows 环境自动使用 Word COM 提取文本，Linux 环境请安装 LibreOffice；
3. **意图识别**：当前为规则式分类（作业/习题/背景/知识点），可按课程实际扩充关键词；
4. **双图谱**：`graph_query` 工具当前基于课程章节顺序提供前置/后继关系，知识图谱、目标图谱完整数据建设后可替换为图谱库查询；
5. 检索阈值 θ、混合权重等为**初始设计值**，需以标准测试集实测调优。
