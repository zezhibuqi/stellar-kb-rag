"""应用配置：从 .env 读取，路径基于项目根目录解析。"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _resolve(value: str) -> str:
    """把配置里的相对路径解析成基于项目根目录的绝对路径。

    .env 里写 `./backend/data/app.db` 时，进程的工作目录可能是 backend/ 也可能是
    仓库根目录；统一按 PROJECT_ROOT 解析，避免两种启动方式读到不同的库。
    """
    path = Path(value)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())


class Config:
    """集中管理环境变量与默认值。"""

    # JWT
    SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "168"))

    # CORS
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("FLASK_CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ]

    # AI 服务
    SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
    SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    SCNET_API_KEY = os.getenv("SCNET_API_KEY", "")
    SCNET_BASE_URL = os.getenv("SCNET_BASE_URL", "https://api.scnet.cn/api/llm/v1")
    XIAOMI_API_KEY = os.getenv("XIAOMI_API_KEY", "")
    XIAOMI_BASE_URL = os.getenv("XIAOMI_BASE_URL", "https://api.xiaomimimo.com/v1")

    # 当前模型的默认值（管理员可在界面切换，DB 设置优先）
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek-v4f")

    # ── 新增 LLM 提供方示例：与此配套还需在 llm.py 的 _build_providers()
    #    注册 ModelProvider（模型标识直接写在注册表），并在 .env 填写密钥
    #    （见 .env.example 模板段）。同平台多模型复用同一组密钥与地址即可。──
    # SILICONFLOW_LLM_KEY = os.getenv("SILICONFLOW_LLM_KEY", "")
    # SILICONFLOW_LLM_BASE_URL = os.getenv("SILICONFLOW_LLM_BASE_URL", "https://api.siliconflow.cn/v1")

    # 数据路径
    CHROMA_PERSIST_DIR = _resolve(os.getenv("CHROMA_PERSIST_DIR", "./backend/data/chroma"))
    DATABASE_URL = _resolve(os.getenv("DATABASE_URL", "./backend/data/app.db"))
    DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "30"))

    # 切片
    MAX_CHUNK_SIZE = int(os.getenv("MAX_CHUNK_SIZE", "1024"))
    MAX_CHUNK_OVERLAP = int(os.getenv("MAX_CHUNK_OVERLAP", "50"))

    # 证据单元（ADR 0009：注入上下文的原文范围上限）
    AGENT_EVIDENCE_MAX_CHARS = int(os.getenv("AGENT_EVIDENCE_MAX_CHARS", "3000"))

    # 会话上下文（服务端统一截取的轮数）
    CHAT_HISTORY_TURNS = int(os.getenv("CHAT_HISTORY_TURNS", "10"))

    # 增强模式（编排式问答）
    AGENT_MAX_SUB_QUESTIONS = int(os.getenv("AGENT_MAX_SUB_QUESTIONS", "8"))
    AGENT_EVIDENCE_PER_SUB = int(os.getenv("AGENT_EVIDENCE_PER_SUB", "3"))
    AGENT_EVIDENCE_GLOBAL = int(os.getenv("AGENT_EVIDENCE_GLOBAL", "10"))
    AGENT_SEARCH_CANDIDATES = int(os.getenv("AGENT_SEARCH_CANDIDATES", "5"))
    # 重排后保留的候选数：实测目标块可能排在密集召回的 8~10 名，
    # 留太窄会在重排阶段被挤掉（它是候选生成之后、注入之前的最后一道闸）
    AGENT_RERANK_TOP_N = int(os.getenv("AGENT_RERANK_TOP_N", "12"))
    # 增强模式的向量召回条数：比标准模式大，缓解超大文档（如 600KB 年报
    # 切出 500+ 块）里具体句子挤不进候选集的问题；重排仍只取少量候选。
    AGENT_RETRIEVE_K = int(os.getenv("AGENT_RETRIEVE_K", "30"))
    # 关键词通道（ADR 0010）：两路各自召回后按 RRF 融合
    AGENT_KEYWORD_TOP_K = int(os.getenv("AGENT_KEYWORD_TOP_K", "80"))
    AGENT_FUSION_TOP_N = int(os.getenv("AGENT_FUSION_TOP_N", "30"))
    AGENT_LIKE_TOP_K = int(os.getenv("AGENT_LIKE_TOP_K", "20"))
    # RRF 融合常数：只用两路的名次，不比较各自的分值
    AGENT_RRF_K = int(os.getenv("AGENT_RRF_K", "60"))
    # 工具返回的候选里为「关键词字面命中」保留的席位数：实测释义表这类
    # 字面精确但语义分低的块会被重排整体淘汰，需要席位保护
    AGENT_KEYWORD_RESERVED = int(os.getenv("AGENT_KEYWORD_RESERVED", "2"))
    # 第二轮的两个独立预算池（ADR 0010）：链式义务不该被机会主义的补查抢占。
    # 链式池按需求动态计算（每子问题配额 × 链式子问题数），这里的值是**安全阀**：
    # 规划器拆出的子问题数量是可变的，固定池必然在某些轮次饿死链式义务。
    AGENT_CHAIN_EVIDENCE_BUDGET = int(
        os.getenv("AGENT_CHAIN_EVIDENCE_BUDGET", "15")
    )
    AGENT_GAP_EVIDENCE_BUDGET = int(os.getenv("AGENT_GAP_EVIDENCE_BUDGET", "4"))
    AGENT_SUB_TIMEOUT = int(os.getenv("AGENT_SUB_TIMEOUT", "60"))
    AGENT_TOTAL_BUDGET = int(os.getenv("AGENT_TOTAL_BUDGET", "120"))
    # 调用 token 预算：思考型模型会先花掉大量预算再输出 JSON。
    # 实测规划一次就用掉约 2900 token（其中 2767 是 reasoning），
    # 因此预算必须显著高于典型值，否则会返回空内容并触发回退。
    AGENT_PLAN_MAX_TOKENS = int(os.getenv("AGENT_PLAN_MAX_TOKENS", "10000"))
    AGENT_SUB_ANSWER_MAX_TOKENS = int(
        os.getenv("AGENT_SUB_ANSWER_MAX_TOKENS", "6000")
    )
    # 合成调用的 token 预算：增强模式的合成提示词包含多个子问题的证据，
    # 比标准模式长得多，沿用 LLM_MAX_TOKENS(4096) 会被推理耗尽、返回空内容
    AGENT_SYNTHESIS_MAX_TOKENS = int(
        os.getenv("AGENT_SYNTHESIS_MAX_TOKENS", "8000")
    )

    # 上传限制
    UPLOAD_MAX_SIZE_MB = int(os.getenv("UPLOAD_MAX_SIZE_MB", "10"))

    # LLM 生成参数
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
