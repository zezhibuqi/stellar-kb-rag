# 模型提供方列表随官方模型迭代调整：下线 scnet，替换 DeepSeek 官方与小米的模型标识

**背景（实测）**：2026-10-17 核对各平台 `/models`：DeepSeek 官方只返回 `deepseek-flash` 与 `deepseek-v4-pro`，注册表里的 `deepseek-v4-flash` 已成为由平台自动路由的别名；小米平台已提供 `mimo-v2.6-flash` / `mimo-v2.6-pro`，旧 `mimo-v2.5` 系列将停止支持；国家超算互联网（scnet）不再使用。别名当前可用，但会掩盖模型实际版本，且平台可以在不通知的情况下去掉它，因此不能作为长期依赖。

**决策**：注册表从 5 个提供方调整为 4 个：

- 删除 `scnet-glm5base` 条目，同时删除 `config.py` 的 `SCNET_API_KEY` / `SCNET_BASE_URL` 与 `.env.example` 的对应模板段；本机 `.env` 中已填的 scnet 密钥不删除，只是不再被读取。
- DeepSeek 官方条目改为 `id=deepseek-flash`、`name=DeepSeek-Flash`、`model=deepseek-flash`。id 与模型名一起改：继续保留 `deepseek-v4f` 会把一个已不存在的版本号传播到 DB、接口与评测断点里。
- 小米两条改为 `id=xiaomi-mimov2.6-flash` / `xiaomi-mimov2.6-pro`，`model` 分别为 `mimo-v2.6-flash` / `mimo-v2.6-pro`；`agent_capable=False`（暂不开放增强模式），`router_max_tokens=2000`。
- 不引入旧 id 别名映射：已失效的 DB 设置沿用既有「回退默认提供方」行为，避免静默把旧模型替换成另一个模型。
- `/api/health` 的 `llm_provider` 增加 `stored`（DB 原值）、`active`（实际生效）与 `fallback`（是否发生回退），让失效设置可见；本机 DB 的 `llm_provider` 同步规范为 `deepseek-flash`。

**Considered Options**：保留 scnet 条目但隐藏（死配置会误导后续维护者，且配置项会继续被误以为受支持）；只改 `model`、保留旧 id（id 会永久携带错误的版本信息）；旧 id → 新 id 自动映射（会静默替换模型，污染模型对比与评测报告）；直接为小米 v2.6 开放增强模式（缺少 JSON 稳定性与延迟的实测证据，且增强模式对模型能力要求更高）；重跑并覆盖历史多模型评测报告（成本高，且历史数据本身是有效的对照材料）。

**Consequences**：`/settings` 与 `/api/settings/model` 返回 4 个提供方；历史报告 `docs/model_comparison_report.md` 与历史 ADR 0006/0008 按原样保留，报告标注「评测时的注册表版本」；`.env` 中的 `SCNET_*` 成为无效配置但无害；若 DB 仍保存 `deepseek` / `deepseek-v4f` / `xiaomi-mimov2.5*` / `scnet-glm5base`，会回退到默认提供方并可经健康检查的 `fallback` 发现；能力标志（关闭 `response_format` / `stream_usage`）的测试覆盖改为注入临时提供方，不再依赖具体平台。
