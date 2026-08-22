# LLM 采用预设提供方注册表且管理员可运行时切换

LLM 调用（回答生成与意图路由）统一走代码内硬编码的预设提供方注册表（DeepSeek、超算互联网 GLM-5-Base）；"当前模型"存于 SQLite `app_settings` 表，管理员通过 `/settings` 页面切换，立即对后续请求生效；切换时校验目标提供方的 API Key 已配置（密钥只来自 `.env`，不入库、不回显），运行时调用失败不自动回退到另一模型，流式失败以 SSE `error` 事件告知前端。

**Considered Options**：管理员界面自定义任意 OpenAI 兼容端点（灵活但密钥入库需加密、连通性与恶意端点校验复杂，超出毕设需要）；当前模型存 `.env` 重启生效（实现最简但违背"界面切换"诉求）；路由模型独立配置（`ROUTER_MODEL`，沿用会形成两个"当前模型"概念，已移除）；运行时失败自动回退默认模型（静默换模型会污染评测对比数据，且两模型回答质量不可比）。

**Consequences**：新增模型需改代码注册（一行注册 + 一把 `.env` 密钥）；切换仅影响后续请求，进行中的回答不受影响；`eval_orders.py` 报告标注当时模型名，支持跨模型对比实验；免费额度平台的限流/断流风险由"测试连接"按钮与 SSE error 事件显式暴露，而非静默降级。提供方能力差异在注册表中显式声明：GLM-5-Base 为思考型模型（先输出 `reasoning_content` 再输出 `content`，网关不响应 `enable_thinking`/`thinking` 关闭参数），故路由调用按提供方预留 `router_max_tokens=2000` 推理预算，且该端点上 `response_format=json_object` 会返回乱序文本（finish=abort），注册为 `supports_response_format=False` 直接跳过；`invoke`/`stream` 在内容为空且被截断时显式报错而非返回空回答。
