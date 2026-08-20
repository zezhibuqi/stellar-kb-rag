"""应用配置：从 .env 读取，路径基于项目根目录解析。"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _resolve(value: str) -> str:
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
    ROUTER_MODEL = os.getenv("ROUTER_MODEL", "deepseek-v4-flash")

    # 数据路径
    CHROMA_PERSIST_DIR = _resolve(os.getenv("CHROMA_PERSIST_DIR", "./backend/data/chroma"))
    DATABASE_URL = _resolve(os.getenv("DATABASE_URL", "./backend/data/app.db"))
    DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "30"))

    # 切片
    MAX_CHUNK_SIZE = int(os.getenv("MAX_CHUNK_SIZE", "1024"))
    MAX_CHUNK_OVERLAP = int(os.getenv("MAX_CHUNK_OVERLAP", "50"))

    # 上传限制
    UPLOAD_MAX_SIZE_MB = int(os.getenv("UPLOAD_MAX_SIZE_MB", "10"))

    # LLM 生成参数
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
