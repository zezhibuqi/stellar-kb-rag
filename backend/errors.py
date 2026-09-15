"""统一错误响应与错误处理器（设计文档 6.5）。"""

from flask import jsonify


def api_error(message: str, code: str, status: int):
    """返回统一格式的错误响应。"""
    return jsonify({"error": message, "code": code}), status


def register_error_handlers(app) -> None:
    """给 Flask 应用注册统一错误处理器（设计文档 6.5）。

    目的是让所有失败路径都返回同一种 JSON 形状 `{"error", "code"}`，
    前端只需实现一套错误解析逻辑。
    """

    @app.errorhandler(ValueError)
    def handle_value_error(exc: ValueError):
        """业务层抛出的 ValueError 一律视作请求参数问题。"""
        return api_error(str(exc), "BAD_REQUEST", 400)

    @app.errorhandler(404)
    def handle_not_found(_):
        """路由不存在（蓝图未命中）。"""
        return api_error("资源不存在", "NOT_FOUND", 404)

    @app.errorhandler(405)
    def handle_method_not_allowed(_):
        """路径存在但 HTTP 方法用错。"""
        return api_error("方法不允许", "METHOD_NOT_ALLOWED", 405)

    @app.errorhandler(500)
    def handle_server_error(exc):
        """兜底 500：先记完整堆栈再返回通用话术，不把内部细节透给前端。"""
        app.logger.exception("未捕获异常", exc_info=exc)
        return api_error("服务器内部错误", "INTERNAL_ERROR", 500)
