"""统一错误响应与错误处理器（设计文档 6.5）。"""

from flask import jsonify


def api_error(message: str, code: str, status: int):
    """返回统一格式的错误响应。"""
    return jsonify({"error": message, "code": code}), status


def register_error_handlers(app) -> None:
    @app.errorhandler(ValueError)
    def handle_value_error(exc: ValueError):
        return api_error(str(exc), "BAD_REQUEST", 400)

    @app.errorhandler(404)
    def handle_not_found(_):
        return api_error("资源不存在", "NOT_FOUND", 404)

    @app.errorhandler(405)
    def handle_method_not_allowed(_):
        return api_error("方法不允许", "METHOD_NOT_ALLOWED", 405)

    @app.errorhandler(500)
    def handle_server_error(exc):
        app.logger.exception("未捕获异常", exc_info=exc)
        return api_error("服务器内部错误", "INTERNAL_ERROR", 500)
