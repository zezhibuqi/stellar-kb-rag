# 企业知识问答系统（RAG）

基于 Retrieval-Augmented Generation（RAG）的多领域、多角色企业内部知识问答平台。支持财务、规章、产品、售后、公共知识五大领域，通过角色权限隔离数据，提供高质量、可溯源的智能问答服务。

> **毕业设计项目** —— 面向企业真实场景的智能化知识管理解决方案

---

##  功能特性

- **多领域知识覆盖**：财务数据、规章制度、产品规格、售后政策、公共知识
- **细粒度权限控制**：5 类角色（普通员工、财务、销售、售后、管理员），按角色自动过滤可访问领域
- **混合检索增强**：向量检索 + 重排序（Reranker），提升答案准确性
- **结构化表格处理**：Markdown 表格完整保留，避免切片破坏语义
- **智能问答生成**：基于 DeepSeek 大模型，支持流式（SSE）输出
- **异步文档入库**：Web 端上传后后台处理，避免超时；支持状态轮询
- **离线灌库脚本**：批量处理 Markdown 文件，便于初始化数据
- **管理员面板**：文档上传/删除/列表，用户创建与角色管理

---

##  技术栈

| 层级 | 组件 | 选型 |
|:---|:---|:---|
| **前端** | 框架 | Next.js 14 (App Router) + TypeScript |
| | UI 库 | Ant Design 5.x |
| **后端** | Web 框架 | Flask ≥3.0 + Flask-CORS |
| | RAG 框架 | LangChain 0.3.x（锁定版本） |
| | 向量数据库 | Chroma ≥0.5（单 Collection + metadata 过滤） |
| | 结构化数据 | SQLite（WAL 模式支持并发） |
| **AI 模型** | Embedding | `BAAI/bge-m3`（SiliconFlow API） |
| | Reranker | `BAAI/bge-reranker-v2-m3`（自定义封装） |
| | LLM | `deepseek-v4-flash`（DeepSeek API，流式支持） |

---

##  项目结构

```
enterprise-kb-qa/
├── backend/
│   ├── app.py                 # Flask 主入口
│   ├── auth.py                # JWT 认证 + 角色装饰器
│   ├── rag.py                 # 检索 + 重排 + LLM 管线
│   ├── ingest.py              # 离线灌库脚本
│   ├── models.py              # SQLite 数据库操作
│   ├── config.py              # 配置管理（读取 .env）
│   ├── reranker.py            # SiliconFlow Reranker 封装
│   ├── data/                  # 本地持久化
│   │   ├── chroma/            # Chroma 向量数据
│   │   └── app.db             # SQLite 数据库
│   ├── markdown_src/          # 源 Markdown 文件（按领域分目录）
│   │   ├── finance/
│   │   ├── regulation/
│   │   ├── product/
│   │   ├── aftersale/
│   │   └── common/
│   └── tests/                 # 单元测试
├── frontend/
│   ├── app/
│   │   ├── layout.tsx         # 全局布局
│   │   ├── login/page.tsx     # 登录页
│   │   ├── chat/page.tsx      # 问答页
│   │   ├── knowledge/page.tsx # 知识库管理（管理员）
│   │   └── users/page.tsx     # 用户管理（管理员）
│   ├── components/
│   │   ├── ChatBox.tsx
│   │   ├── SourceCard.tsx
│   │   └── LayoutWrapper.tsx
│   └── lib/api.ts             # API 调用（含 SSE 消费）
├── .env                       # 环境变量（不提交）
├── requirements.txt
└── README.md
```

---

##  快速启动（本地开发）

### 1. 环境要求
- Python ≥3.10
- Node.js ≥18
- npm / yarn / pnpm

### 2. 克隆仓库
```bash
git clone https://github.com/your-username/enterprise-kb-qa.git
cd enterprise-kb-qa
```

### 3. 后端配置与运行

#### 安装依赖
```bash
# 在项目根目录执行
pip install -r requirements.txt
```

#### 环境变量
复制 `.env.example` 为 `.env` 并填入真实 API Key 及配置项：
```bash
cp .env.example .env
```

必须配置的变量（详见 `.env.example`）：
- `SILICONFLOW_API_KEY`：硅基流动 API 密钥
- `DEEPSEEK_API_KEY`：DeepSeek API 密钥
- `JWT_SECRET_KEY`：随机字符串（≥32 字符）

#### 初始化数据库
```bash
cd backend
python -c "from models import init_db; init_db()"
```

#### 启动后端
```bash
flask run --port=5000
```
后端将在 `http://localhost:5000` 运行。

> 如需离线灌库初始数据，使用：
> ```bash
> python ingest.py --domain finance --path markdown_src/finance
> ```

### 4. 前端配置与运行

```bash
cd frontend
npm install
npm run dev
```
前端将在 `http://localhost:3000` 运行。

---

##  默认账户

系统初始化时会创建以下测试账户（密码均为 `123456`）：

| 用户名 | 角色 | 可访问领域 |
|:---|:---|:---|
| `admin` | 管理员 | 全部 |
| `employee` | 普通员工 | common, regulation |
| `finance` | 财务 | common, finance, regulation |
| `sales` | 销售 | common, product, regulation |
| `aftersale` | 售后 | common, aftersale, regulation |

> 💡 生产环境请务必修改默认密码或删除测试账户。

---

## 📡 API 概览

| 方法 | 路径 | 说明 | 权限 |
|:---|:---|:---|:---|
| POST | `/api/auth/login` | 登录 | 公开 |
| GET | `/api/auth/me` | 获取当前用户信息 | 已登录 |
| POST | `/api/chat` | 问答（支持流式 SSE） | 已登录 |
| GET | `/api/docs` | 获取文档列表 | 管理员 |
| POST | `/api/upload` | 上传文档（异步灌库，返回 doc_id） | 管理员 |
| GET | `/api/docs/:id/status` | 查询灌库进度 | 管理员 |
| DELETE | `/api/docs/:id` | 删除文档及对应向量 | 管理员 |
| GET | `/api/users` | 用户列表 | 管理员 |
| POST | `/api/users` | 创建用户 | 管理员 |
| PUT | `/api/users/:id/role` | 修改用户角色 | 管理员 |
| GET | `/api/health` | 系统健康检查 | 公开 |

详细接口文档请参考 `项目设计文档 V1.7.md` 第 6 节。

---

##  测试与评测

### 单元测试
```bash
cd backend
pytest tests/
```

### 检索质量评测
1. 准备 Golden Set（每个领域 15-20 个问答对），格式见设计文档 10.1 节。
2. 运行评测脚本：
```bash
python tests/evaluate.py --golden golden_set.json
```
输出 Hit Rate 和 MRR 指标。

---

##  部署（云服务器）

### 后端
- 使用 Gunicorn + Nginx 反向代理（需关闭 `proxy_buffering` 以支持 SSE）
- Chroma 可改为独立服务：`docker run -p 8000:8000 chromadb/chroma`
- 修改后端配置连接远程 Chroma

### 前端
- 构建静态文件：`npm run build && npm run export`
- 部署到 Nginx 或 Vercel / Netlify

详细部署方案见设计文档第 11 节。

---

##  数据说明

本项目所有数据来源于公开渠道，经脱敏和虚构改写（如财务数值），统一归集至虚构的“星辰科技集团”背景下，**仅供学术研究使用**。

---

## 贡献指南

本仓库为毕业设计项目，目前仅限开发者本人维护。欢迎提出 Issue 或改进建议，但暂不接受外部 PR。

---
