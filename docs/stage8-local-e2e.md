# Stage 8 本地全流程联调报告

- 日期：2026-08-07
- 范围：仅本地端到端联调；线上部署按用户要求推迟（约一年后），本报告不含部署内容。
- 状态：全部通过

## 联调旅程（与开发文档 Stage 8 本地验证一致）

1. `GET /api/health`：`status=ok`，sqlite / chroma / siliconflow_key / deepseek_key 均正常。
2. `POST /api/auth/login`：admin 登录成功。
3. `POST /api/users`：admin 创建 employee 用户（`e2e_205748`）。
4. 新用户 `POST /api/auth/login` + `GET /api/auth/me`：角色为 `employee`。
5. `POST /api/upload`（`企业文化与价值观手册.md`，common）：返回 `202 {doc_id: 1, status: "pending"}`。
6. 轮询 `GET /api/docs/1/status`：`pending → completed`，Chroma 入库完成。
7. `POST /api/chat`（非流式）：返回真实答案「公司的核心价值观是诚信为本、创新驱动、客户至上、绿色共赢」，引用来源完整。
8. `POST /api/chat`（`stream=true`）：SSE 共 18 个 token 事件 + done 事件（5 条 sources）。
9. 越权验证：employee 提问「2025年净利润是多少？」仅返回 common 领域来源，无 finance 泄漏。
10. `DELETE /api/docs/1`：删除成功，列表与 Chroma 向量同步清除。

## 复现命令

```bash
# 终端 1：启动后端（需 .env 配置 SILICONFLOW_API_KEY / DEEPSEEK_API_KEY / JWT_SECRET_KEY）
cd backend
..\.venv\Scripts\python.exe -m flask --app app run --port 5000

# 终端 2：执行自动化联调（真实 API）
..\.venv\Scripts\python.exe backend\e2e_demo.py --file backend\markdown_src\common\企业文化与价值观手册.md --domain common

# 自动化测试（Mock 外部 API）
..\.venv\Scripts\python.exe -m pytest backend/tests -q
```

## 结果

- `pytest backend/tests -q`：60 passed。
- 真实联调脚本：10 个步骤全部 PASS。
- 遗留说明：`docs/eval_report.md` 中 product 领域 Hit Rate 66.67% 低于 80%，已记录改进计划，不影响本地全流程功能验收；线上部署、Nginx/Gunicorn/Chroma 服务端切换与答辩素材留待后续代理处理。
