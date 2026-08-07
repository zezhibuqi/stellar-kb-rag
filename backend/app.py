"""Flask 应用工厂：CORS 配置、健康检查、后续蓝图注册入口。"""

import sqlite3
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from config import Config


def check_components() -> dict:
    """返回各组件健康状态；API Key 缺失时状态为 missing。"""
    cfg = Config()
    components: dict = {}

    try:
        conn = sqlite3.connect(cfg.DATABASE_URL, timeout=cfg.DATABASE_TIMEOUT)
        conn.execute("SELECT 1")
        conn.close()
        components["sqlite"] = {"status": "connected", "path": cfg.DATABASE_URL}
    except Exception as exc:  # noqa: BLE001 - 健康检查需捕获所有异常
        components["sqlite"] = {"status": "error", "message": str(exc)}

    try:
        Path(cfg.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
        components["chroma"] = {"status": "connected", "path": cfg.CHROMA_PERSIST_DIR}
    except Exception as exc:  # noqa: BLE001
        components["chroma"] = {"status": "error", "message": str(exc)}

    components["siliconflow_key"] = {
        "status": "configured" if cfg.SILICONFLOW_API_KEY else "missing"
    }
    components["deepseek_key"] = {
        "status": "configured" if cfg.DEEPSEEK_API_KEY else "missing"
    }
    return components


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config or Config)
    CORS(app, origins=app.config["CORS_ORIGINS"])

    @app.get("/api/health")
    def health():
        components = check_components()
        status = (
            "ok"
            if all(item["status"] in ("connected", "configured") for item in components.values())
            else "degraded"
        )
        return jsonify({"status": status, **components})

    # 后续阶段在此注册蓝图：auth_bp / users_bp / docs_bp / chat_bp
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
