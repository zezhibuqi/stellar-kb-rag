# 企业知识问答系统（RAG）

基于 Retrieval-Augmented Generation（RAG）的多领域、多角色企业内部知识问答平台。支持财务、规章、产品、售后、公共知识五大领域，通过角色权限隔离数据，提供高质量、可溯源的智能问答服务。

> **毕业设计项目** —— 面向企业真实场景的智能化知识管理解决方案
> 
> **项目状态**：Stage 0~12 本地开发与全流程联调已完成（含增强模式、会话与消息、双通道检索改进）；线上部署暂缓（约一年后进行）。

---

## 功能特性

- **多领域知识覆盖**：财务数据、规章制度、产品规格、售后政策、公共知识
- **细粒度权限控制**：5 类角色（普通员工、财务、销售、售后、管理员），按角色自动过滤可访问领域
- **混合检索增强**：向量检索（bge-m3）+ 重排序（bge-reranker-v2-m3），提升答案准确性
- **双通道检索（增强模式）**：向量通道与 BM25 关键词通道（SQLite FTS5 + trigram，专治型号、数字等字面目标）并行召回，按 RRF 融合后再重排；关键词索引随灌库增量维护，`/api/health` 暴露未入索引的文档数
- **增强模式（编排式问答）**：把问题拆成可独立检索的子问题并行作答，再做有界第二轮补充检索（链式延伸 + 缺口补查），最后逐项合成；前端「分析过程」容器实时展示子问题与证据覆盖，来源按子问题分组；仅支持流式，且要求当前模型具备 `agent_capable`
- **会话与消息**：每用户最多保留 5 个会话，上下文由服务端统一截取最近 10 轮；消息带模式与状态（`streaming` / `completed` / `failed` / `aborted`），可回放分析与来源
- **结构化表格处理**：Markdown 表格完整保留，避免切片破坏语义
- **智能问答生成**：基于 LLM 生成回答，支持流式（SSE）输出；生成失败显式提示而非静默断流
- **管理员可切换模型**：预设提供方注册表（OpenAI 兼容协议，内置 DeepSeek、硅基流动、超算互联网、小米平台的 5 个模型），界面切换当前模型并即时生效，支持连通性测试；思考型模型的推理预算与 `response_format` 兼容性在注册表中按提供方声明
- **异步文档入库**：Web 端上传后后台处理，避免超时；支持状态轮询
- **离线灌库脚本**：批量处理 Markdown 文件，便于初始化数据
- **管理员面板**：文档上传/删除/列表，用户创建/角色管理/停用启用/永久删除/重置密码
- **自助修改密码**：所有登录用户可在右上角菜单修改自己密码（验证当前密码，修改后其他会话自动失效）
- **原文查看与定位**：引用来源 Markdown 渲染，一键打开原文档并高亮定位引用片段
- **订单结构化问答**：aftersale/admin 可用自然语言精确查询订单（单订单/客户/条件过滤/聚合），结果来自数据库并带“数据库”来源标记
- **订单数据页面**：aftersale/admin 可浏览/筛选订单数据库列表（分页、联系方式脱敏）

---

## 技术栈（关键版本已锁定）

| 层级        | 组件        | 选型                                                                     |
|:--------- |:--------- |:---------------------------------------------------------------------- |
| **前端**    | 框架        | Next.js 14.2 (App Router) + TypeScript                                 |
|           | UI 库      | Ant Design 5.29                                                        |
| **后端**    | Web 框架    | Flask 3.1 + Flask-CORS                                                 |
|           | RAG 框架    | LangChain 0.3.30（`langchain-chroma` / `langchain-community`）           |
|           | 向量数据库     | Chroma 1.5.9（单一 Collection + metadata 过滤）                              |
|           | 结构化数据     | SQLite（WAL 模式 + 每线程独立连接）                                               |
| **AI 模型** | Embedding | `BAAI/bge-m3`（SiliconFlow API，1024 维）                                  |
|           | Reranker  | `BAAI/bge-reranker-v2-m3`（自定义封装）                                       |
|           | LLM       | OpenAI 兼容接口，内置 5 个模型：DeepSeek-V4-Flash-0731（DeepSeek）、GLM-5-Base（超算互联网 scnet）、DeepSeek-V4-Flash-0731（硅基流动）、MIMO-V2.5 / MIMO-V2.5-Pro（小米）；管理员界面切换，流式支持，新增 OpenAI 兼容模型只需在注册表登记 |

---

## 项目结构

```
stellar-kb-rag/
├── backend/
│   ├── app.py              # Flask 应用工厂 + 蓝图注册 + 健康检查
│   ├── config.py           # 配置管理（读取 .env）
│   ├── errors.py           # 统一错误响应
│   ├── auth.py             # JWT 认证 + 角色装饰器
│   ├── users_api.py        # 用户管理接口（仅 admin）
│   ├── docs_api.py         # 知识库管理接口（上传/列表/状态/删除/原文）
│   ├── chat_api.py         # 问答接口（模式校验 + 非流式/SSE + 消息落库）
│   ├── conversations_api.py  # 会话接口（列表/新建/回放/删除）
│   ├── tasks.py            # 后台灌库线程池与真实流水线
│   ├── splitter.py         # Markdown 切片器（文本/表格 + 证据单元行区间）
│   ├── embeddings.py       # SiliconFlow Embedding + LangChain 适配
│   ├── chroma_store.py     # Chroma 单一 Collection 存取 + 证据单元展开/去重
│   ├── keyword_index.py    # BM25 关键词通道（FTS5 + trigram + LIKE 兜底）
│   ├── reranker.py         # SiliconFlow Reranker 封装
│   ├── llm.py              # LLM 客户端（预设提供方注册表 + 当前模型解析）
│   ├── rag.py              # 标准模式：权限检索 + 重排 + 问答组装
│   ├── agent_tools.py      # 增强模式工具（知识检索 / 订单查询）
│   ├── orchestrator.py     # 增强模式编排（规划/子答案/有界第二轮/合成）
│   ├── models.py           # SQLite 数据访问层（Schema/种子/CRUD）
│   ├── order_qa.py         # 订单意图路由 + 参数化 SQL 执行器
│   ├── orders_seed.py      # 订单种子数据（确定性、幂等）
│   ├── orders_api.py       # 订单数据列表接口（过滤/分页/脱敏）
│   ├── settings_api.py     # 模型设置接口（查看/切换/测试，仅 admin）
│   ├── ingest.py           # 离线灌库 CLI
│   ├── eval.py             # 检索质量评测脚本
│   ├── eval_orders.py      # 订单结构化问答评测脚本
│   ├── eval_models.py      # 多模型对比评测脚本
│   ├── e2e_demo.py         # 本地端到端联调脚本
│   ├── demo_enhanced.py    # 增强模式演示脚本（观察 stage 事件与来源）
│   ├── demo_retrieve.py    # 离线检索演示脚本（权限过滤 + 重排）
│   ├── data/               # 运行时数据（app.db/chroma，gitignored；原文档全文存于 app.db）
│   ├── markdown_src/       # 源 Markdown（按领域分目录，gitignored）
│   └── tests/              # 自动化测试（232 个用例）
├── frontend/
│   ├── app/                # login / chat / knowledge / users / viewer / orders / settings 页面
│   ├── components/         # LayoutWrapper / ChatBox / ConversationList / AnalysisPanel / SourceCard / ThemeProvider
│   └── lib/api.ts          # API 封装（含 SSE 消费）
├── docs/                   # ADR、Golden Set、评测报告、联调报告
├── CONTEXT.md              # 领域词表
├── .env.example            # 环境变量模板
├── requirements.txt
└── README.md
```

---

## 快速启动（本地开发）

### 0. 一键启动（Windows）

在项目根目录双击或执行 `start-dev.cmd`：脚本会自动初始化数据库，并分别弹出后端（:5000）与前端（:3000）窗口；若端口已被占用会自动跳过。

脚本的前置条件：根目录已有 `.venv` 与 `.env`、`frontend/node_modules` 已安装；缺任一项会给出提示并退出，不会静默失败。

### 1. 环境要求

- Python ≥ 3.10
- Node.js ≥ 18
- npm（Windows 下请使用 `npm.cmd`，PowerShell 默认禁止执行 `npm.ps1`）

### 2. 克隆仓库

```bash
git clone https://github.com/zezhibuqi/stellar-kb-rag.git
cd stellar-kb-rag
```

### 3. 后端配置与运行

```bash
# 创建虚拟环境并安装依赖（在项目根目录）
python -m venv .venv
# Windows 激活：.venv\Scripts\Activate.ps1；或直接使用 .venv\Scripts\python.exe
pip install -r requirements.txt

# 复制环境变量模板并填写真实 Key
Copy-Item .env.example .env   # Windows
# cp .env.example .env        # Linux / macOS
```

必须配置的变量（详见 `.env.example`）：

- `SILICONFLOW_API_KEY`：硅基流动 API 密钥
- `DEEPSEEK_API_KEY`：DeepSeek API 密钥
- `SCNET_API_KEY`：超算互联网（scnet）API 密钥（使用 GLM-5-Base 时必填；不填则该模型在界面中不可切换）
- `XIAOMI_API_KEY`：小米 API 密钥（使用 MIMO-V2.5 / MIMO-V2.5-Pro 时必填；不填则该模型在界面中不可切换）
- `JWT_SECRET_KEY`：随机字符串（≥32 字符）
- `LLM_PROVIDER`：当前模型的默认 id（须为注册表中已存在的 id，如 `deepseek-v4f`、`scnet-glm5base`），管理员界面切换后以 DB 设置为准

初始化数据库并启动后端：

```bash
cd backend
..\.venv\Scripts\python.exe -c "from models import init_db; init_db()"
..\.venv\Scripts\python.exe -m flask --app app run --port 5000
```

后端将在 `http://localhost:5000` 运行。如需离线灌库初始数据：

```bash
..\.venv\Scripts\python.exe ingest.py --domain finance --path markdown_src/finance
```

### 4. 前端配置与运行

```bash
cd frontend
npm install
npm.cmd run dev   # 其他平台用 npm run dev
```

前端将在 `http://localhost:3000` 运行。

---

## 默认账户

系统初始化时会创建以下测试账户（密码均为 `123456`）：

| 用户名         | 角色   | 可访问领域                         |
|:----------- |:---- |:----------------------------- |
| `admin`     | 管理员  | 全部                            |
| `employee`  | 普通员工 | common, regulation            |
| `finance`   | 财务   | common, finance, regulation   |
| `sales`     | 销售   | common, product, regulation   |
| `aftersale` | 售后   | common, aftersale, regulation |

>  系统不提供公开注册，用户由管理员在用户管理页创建。生产环境请务必修改默认密码或删除测试账户。

---

## API 概览

| 方法     | 路径                          | 说明                       | 权限              |
|:------ |:--------------------------- |:------------------------ |:--------------- |
| POST   | `/api/auth/login`           | 登录                       | 公开              |
| GET    | `/api/auth/me`              | 获取当前用户信息                 | 已登录             |
| POST   | `/api/chat`                 | 问答（支持流式 SSE）             | 已登录             |
| GET    | `/api/conversations`        | 我的会话列表                   | 已登录             |
| POST   | `/api/conversations`        | 新建会话（每用户上限 5 个）          | 已登录             |
| GET    | `/api/conversations/:id/messages` | 回放会话消息（含来源与过程记录）    | 仅本人会话           |
| DELETE | `/api/conversations/:id`    | 删除会话（消息级联删除）            | 仅本人会话           |
| GET    | `/api/docs`                 | 获取文档列表                   | 管理员             |
| POST   | `/api/upload`               | 上传文档（异步灌库，返回 doc_id）     | 管理员             |
| GET    | `/api/docs/:id/status`      | 查询灌库进度                   | 管理员             |
| GET    | `/api/docs/:id/raw`         | 获取原文档全文（Markdown）        | 已登录且领域可访问       |
| DELETE | `/api/docs/:id`             | 删除文档及对应向量                | 管理员             |
| GET    | `/api/users`                | 用户列表                     | 管理员             |
| POST   | `/api/users`                | 创建用户                     | 管理员             |
| PUT    | `/api/users/:id/role`       | 修改用户角色                   | 管理员             |
| PUT    | `/api/users/:id/deactivate` | 停用账号                     | 管理员             |
| PUT    | `/api/users/:id/activate`   | 恢复启用账号                   | 管理员             |
| DELETE | `/api/users/:id`            | 永久删除账号（仅限已停用账号）          | 管理员             |
| PUT    | `/api/users/:id/password`   | 重置他人密码（旧 token 失效）       | 管理员             |
| PUT    | `/api/auth/password`        | 自助修改密码（验证当前密码，返回新 token） | 登录用户            |
| GET    | `/api/orders`               | 订单数据列表（过滤+分页，联系方式脱敏）     | aftersale/admin |
| GET    | `/api/settings/model`       | 查看模型提供方与当前模型             | 管理员             |
| PUT    | `/api/settings/model`       | 切换当前模型                   | 管理员             |
| POST   | `/api/settings/model/test`  | 模型连通性测试                  | 管理员             |
| GET    | `/api/health`               | 系统健康检查                   | 公开              |

详细接口文档请参考 `项目设计文档 V2.0.md` 第 6 节。

---

## 新增 LLM 模型提供方

LLM 通过 `backend/llm.py` 中的**预设提供方注册表**管理，全部走 OpenAI 兼容接口。已内置 5 个模型：DeepSeek 的 DeepSeek-V4-Flash-0731（`deepseek-v4f`）、硅基流动的 DeepSeek-V4-Flash-0731（`siliconflow-dsv4f`）、超算互联网的 GLM-5-Base（`scnet-glm5base`）、小米的 MIMO-V2.5 / MIMO-V2.5-Pro（`xiaomi-mimov2.5`、`xiaomi-mimov2.5pro`）。任何 OpenAI 兼容协议的模型均可接入——模型设置页面与接口均从注册表动态渲染，**新增模型只需三步、无需改动前端**：

1. **注册提供方**：在 `llm.py` 的 `_build_providers()` 列表中新增一项 `ModelProvider(...)`（文件内有完整注释示例，可参考抄改）；**模型标识直接写在 `model` 字段**，同平台多模型只需 `id` 唯一、`base_url` 与密钥可复用；
2. **添加配置项**：在 `config.py` 中为该平台的密钥与接口地址新增环境变量映射（同样有注释示例）；
3. **配置密钥**：在 `.env` 中填入真实密钥（`.env.example` 有对应模板段），重启后端。

完成后 `/settings` 页面会自动出现新模型卡片，可直接测试连接与切换。

**能力标志提示**（注册表字段，来自 GLM-5-Base 的实测经验）：

- 普通对话模型（DeepSeek、DeepSeek-V4-Flash@SiliconFlow 等）：默认参数即可；
- **思考型模型**（先输出 `reasoning_content` 再输出 `content`）：路由调用需预留推理预算，设 `router_max_tokens=2000`；若该端点传 `response_format=json_object` 会报错或返回乱码，设 `supports_response_format=False`。判断方法：切换后问一句订单问题，失败时看后端日志与"测试连接"结果定位。
- **流式 token 用量**：`supports_stream_usage` 默认 `True`，增强模式的流式调用会带 `stream_options.include_usage` 以把每步 token 用量写入 trace；端点不认识该参数时注册为 `False`（如 scnet），trace 里该步用量留空，回答不受影响。

---

## 测试与评测

```bash
# 单元/接口/评测逻辑测试（232 个用例）
.\.venv\Scripts\python.exe -m pytest backend/tests -q

# 本地端到端联调（需先启动后端）
.\.venv\Scripts\python.exe backend\e2e_demo.py

# 增强模式演示（需先启动后端；观察 planning/planned/sub_answer/synthesizing 事件，可加 --mode standard 对照）
.\.venv\Scripts\python.exe backend\demo_enhanced.py --question "为 SC-500 工商业储能一体柜供电的那款电芯，它的单体质量能量密度和 25℃ 循环寿命分别是多少？"

# 检索质量评测（Golden Set 75 条）
.\.venv\Scripts\python.exe backend\eval.py --golden docs\golden_set.json --report docs\eval_report.md

# 订单结构化问答评测（Golden Set 21 条）
.\.venv\Scripts\python.exe backend\eval_orders.py --golden docs\golden_orders.json --report docs\eval_orders_report.md

# 多模型对比评测（订单正确率/路由准确率/检索对照/流式延迟；需知识库已灌库）
.\.venv\Scripts\python.exe backend\eval_models.py --report docs\model_comparison_report.md
```

当前评测结果（报告均在 `docs/` 下，各自标注评测日期）：

- 检索质量：Hit Rate 88.00%、MRR 0.8322（2026-08-07，`docs/eval_report.md`，达标；未达标领域 product 66.67%，改进计划见该报告）；另一份 2026-08-26 的检索对照为 Hit Rate 89.33%、MRR 0.8356（`docs/model_comparison_report.md`）。
- 订单结构化问答：答案正确率 95.24%、路由准确率 100%（2026-08-20，`docs/eval_orders_report.md`）。
- 多模型对比：订单答案正确率 90.48%~100%、路由准确率 95.24%~100%、平均首 token 延迟 0.74s~14.84s（2026-08-26，`docs/model_comparison_report.md`）。

---

## 项目文档

- `项目设计文档 V2.0.md`：需求、架构、接口与数据库设计（含增强模式、会话与消息、双通道检索）
- `项目开发文档-V1.0.md`：Stage 0~8 任务与验收标准
- `项目开发文档-V2.0.md`：Stage 9~12 任务与验收标准（切片器与证据单元、会话与消息、增强模式后端与前端）
- `CONTEXT.md`：领域词表
- `docs/adr/`：架构决策记录（单一 Chroma Collection、增强模式、证据单元、双通道检索等）
- `docs/golden_set.json` / `docs/eval_report.md`：Golden Set 与评测报告
- `docs/eval_orders_report.md` / `docs/model_comparison_report.md`：订单问答与多模型对比评测报告
- `docs/stage8-local-e2e.md`：本地全流程联调报告

---

## 部署（暂缓）

线上部署约一年后进行，本仓库当前仅本地运行。届时按 `项目设计文档 V2.0.md` 第 11 节与 `项目开发文档-V1.0.md` 的 Stage 8 执行：

- Chroma 改为独立服务（`docker run -p 8000:8000 chromadb/chroma`）并切换为 `HttpClient`
- 后端使用 Gunicorn + Nginx 反向代理（需关闭 `proxy_buffering` 以支持 SSE）
- 前端 `npm run build` 后静态部署
- 本地数据迁移（注意两端 Chroma 版本保持一致）
- 生产密钥配置与默认账号治理

---

## 数据说明

本项目所有数据来源于公开渠道，经脱敏和虚构改写（如财务数值），统一归集至虚构的“星辰科技集团”背景下，**仅供学术研究使用**。

---

## 贡献指南

本仓库为毕业设计项目，目前仅限开发者本人维护。欢迎提出 Issue 或改进建议，但暂不接受外部 PR。

---
