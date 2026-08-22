# 企业知识问答系统（RAG）

基于 Retrieval-Augmented Generation（RAG）的多领域、多角色企业内部知识问答平台。支持财务、规章、产品、售后、公共知识五大领域，通过角色权限隔离数据，提供高质量、可溯源的智能问答服务。

> **毕业设计项目** —— 面向企业真实场景的智能化知识管理解决方案
>
> **项目状态**：Stage 0~8 本地开发与全流程联调已完成；线上部署暂缓（约一年后进行）。

---

## 功能特性

- **多领域知识覆盖**：财务数据、规章制度、产品规格、售后政策、公共知识
- **细粒度权限控制**：5 类角色（普通员工、财务、销售、售后、管理员），按角色自动过滤可访问领域
- **混合检索增强**：向量检索 + 重排序（Reranker），提升答案准确性
- **结构化表格处理**：Markdown 表格完整保留，避免切片破坏语义
- **智能问答生成**：基于 LLM 生成回答，支持流式（SSE）输出；生成失败显式提示而非静默断流
- **管理员可切换模型**：预设提供方注册表（DeepSeek / 超算互联网 GLM-5-Base），界面切换当前模型并即时生效，支持连通性测试
- **异步文档入库**：Web 端上传后后台处理，避免超时；支持状态轮询
- **离线灌库脚本**：批量处理 Markdown 文件，便于初始化数据
- **管理员面板**：文档上传/删除/列表，用户创建/角色管理/停用启用/重置密码
- **原文查看与定位**：引用来源 Markdown 渲染，一键打开原文档并高亮定位引用片段
- **订单结构化问答**：aftersale/admin 可用自然语言精确查询订单（单订单/客户/条件过滤/聚合），结果来自数据库并带“数据库”来源标记
- **订单数据页面**：aftersale/admin 可浏览/筛选订单数据库列表（分页、联系方式脱敏）

---

## 技术栈（版本已锁定）

| 层级 | 组件 | 选型 |
|:---|:---|:---|
| **前端** | 框架 | Next.js 14.2 (App Router) + TypeScript |
| | UI 库 | Ant Design 5.29 |
| **后端** | Web 框架 | Flask 3.1 + Flask-CORS |
| | RAG 框架 | LangChain 0.3.30（`langchain-chroma` / `langchain-community`） |
| | 向量数据库 | Chroma 1.5.9（单一 Collection + metadata 过滤） |
| | 结构化数据 | SQLite（WAL 模式 + 每线程独立连接） |
| **AI 模型** | Embedding | `BAAI/bge-m3`（SiliconFlow API，1024 维） |
| | Reranker | `BAAI/bge-reranker-v2-m3`（自定义封装） |
| | LLM | `deepseek-v4-flash`（DeepSeek）或 `GLM-5-Base`（超算互联网 scnet），管理员界面可切换，流式支持 |

---

## 项目结构

```
stellar-kb-rag/
├── backend/
│   ├── app.py              # Flask 应用工厂 + 蓝图注册
│   ├── auth.py             # JWT 认证 + 角色装饰器
│   ├── users_api.py        # 用户管理接口（仅 admin）
│   ├── docs_api.py         # 知识库管理接口（上传/列表/状态/删除/原文）
│   ├── chat_api.py         # 问答接口（非流式 + SSE）
│   ├── tasks.py            # 后台灌库线程池与真实流水线
│   ├── splitter.py         # Markdown 切片器（文本/表格）
│   ├── embeddings.py       # SiliconFlow Embedding + LangChain 适配
│   ├── chroma_store.py     # Chroma 单一 Collection 存取
│   ├── reranker.py         # SiliconFlow Reranker 封装
│   ├── llm.py              # LLM 客户端（预设提供方注册表 + 当前模型解析）
│   ├── rag.py              # 权限检索 + 重排 + 问答组装
│   ├── models.py           # SQLite 数据访问层（Schema/种子/CRUD）
│   ├── config.py           # 配置管理（读取 .env）
│   ├── ingest.py           # 离线灌库 CLI
│   ├── eval.py             # Golden Set 评测脚本
│   ├── eval_orders.py      # 订单结构化问答评测脚本
│   ├── order_qa.py         # 订单意图路由 + 参数化 SQL 执行器
│   ├── orders_seed.py      # 订单种子数据（确定性、幂等）
│   ├── orders_api.py       # 订单数据列表接口（过滤/分页/脱敏）
│   ├── settings_api.py     # 模型设置接口（查看/切换/测试，仅 admin）
│   ├── e2e_demo.py         # 本地端到端联调脚本
│   ├── data/               # 运行时数据（app.db/chroma，gitignored；原文档全文存于 app.db）
│   ├── markdown_src/       # 源 Markdown（按领域分目录，gitignored）
│   └── tests/              # 自动化测试（133 个用例）
├── frontend/
│   ├── app/                # login / chat / knowledge / users / viewer / orders / settings 页面
│   ├── components/         # LayoutWrapper / ChatBox / SourceCard
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
- `JWT_SECRET_KEY`：随机字符串（≥32 字符）
- `LLM_PROVIDER`：当前模型提供方默认值（`deepseek` / `scnet`），管理员界面切换后以 DB 设置为准

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

| 用户名 | 角色 | 可访问领域 |
|:---|:---|:---|
| `admin` | 管理员 | 全部 |
| `employee` | 普通员工 | common, regulation |
| `finance` | 财务 | common, finance, regulation |
| `sales` | 销售 | common, product, regulation |
| `aftersale` | 售后 | common, aftersale, regulation |

> 💡 系统不提供公开注册，用户由管理员在用户管理页创建。生产环境请务必修改默认密码或删除测试账户。

---

## API 概览

| 方法 | 路径 | 说明 | 权限 |
|:---|:---|:---|:---|
| POST | `/api/auth/login` | 登录 | 公开 |
| GET | `/api/auth/me` | 获取当前用户信息 | 已登录 |
| POST | `/api/chat` | 问答（支持流式 SSE） | 已登录 |
| GET | `/api/docs` | 获取文档列表 | 管理员 |
| POST | `/api/upload` | 上传文档（异步灌库，返回 doc_id） | 管理员 |
| GET | `/api/docs/:id/status` | 查询灌库进度 | 管理员 |
| GET | `/api/docs/:id/raw` | 获取原文档全文（Markdown） | 已登录且领域可访问 |
| DELETE | `/api/docs/:id` | 删除文档及对应向量 | 管理员 |
| GET | `/api/users` | 用户列表 | 管理员 |
| POST | `/api/users` | 创建用户 | 管理员 |
| PUT | `/api/users/:id/role` | 修改用户角色 | 管理员 |
| DELETE | `/api/users/:id` | 停用账号（软删除） | 管理员 |
| PUT | `/api/users/:id/active` | 恢复启用账号 | 管理员 |
| PUT | `/api/users/:id/password` | 重置密码（旧 token 失效） | 管理员 |
| GET | `/api/orders` | 订单数据列表（过滤+分页，联系方式脱敏） | aftersale/admin |
| GET | `/api/health` | 系统健康检查 | 公开 |

详细接口文档请参考 `项目设计文档 V1.7.md` 第 6 节。

---

## 测试与评测

```bash
# 单元/接口/评测逻辑测试（133 个用例）
.\.venv\Scripts\python.exe -m pytest backend/tests -q

# 本地端到端联调（需先启动后端）
.\.venv\Scripts\python.exe backend\e2e_demo.py

# 检索质量评测（Golden Set 75 条）
.\.venv\Scripts\python.exe backend\eval.py --golden docs\golden_set.json --report docs\eval_report.md

# 订单结构化问答评测（Golden Set 21 条）
.\.venv\Scripts\python.exe backend\eval_orders.py --golden docs\golden_orders.json --report docs\eval_orders_report.md
```

当前评测结果：总体 Hit Rate 88%、MRR 0.83（达标）；product 领域 66.67%，改进计划见 `docs/eval_report.md`。
订单结构化问答：答案正确率 95.24%、路由准确率 100%，报告见 `docs/eval_orders_report.md`。

---

## 项目文档

- `项目设计文档 V1.7.md`：需求、架构、接口与数据库设计
- `项目开发文档-V1.0.md`：Stage 0~8 任务与验收标准
- `CONTEXT.md`：领域词表
- `docs/adr/`：架构决策记录（单一 Chroma Collection、管理员创建用户等）
- `docs/golden_set.json` / `docs/eval_report.md`：Golden Set 与评测报告
- `docs/stage8-local-e2e.md`：本地全流程联调报告

---

## 部署（暂缓）

线上部署约一年后进行，本仓库当前仅本地运行。届时按设计文档第 11 节与开发文档 Stage 8 执行：

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
