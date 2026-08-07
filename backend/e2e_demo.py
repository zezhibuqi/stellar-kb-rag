"""Stage 8 本地端到端联调脚本。

依赖：后端已在 http://127.0.0.1:5000 运行（flask --app app run）。
用法：python e2e_demo.py [--file 路径] [--domain 领域]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5000"


def _check(name: str, condition: bool, extra: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" | {extra}" if extra else ""))
    if not condition:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地端到端联调")
    parser.add_argument(
        "--file",
        default="backend/markdown_src/common/企业文化与价值观手册.md",
        help="要上传的 Markdown 路径",
    )
    parser.add_argument("--domain", default="common", help="知识领域")
    args = parser.parse_args()

    # 0. 健康检查
    health = requests.get(f"{BASE}/api/health", timeout=10).json()
    _check(
        "健康检查 ok 且 Key 已配置",
        health.get("status") == "ok"
        and health.get("siliconflow_key", {}).get("status") == "configured",
        str(health),
    )

    # 1. admin 登录
    resp = requests.post(
        f"{BASE}/api/auth/login",
        json={"username": "admin", "password": "123456"},
        timeout=10,
    )
    _check("admin 登录", resp.status_code == 200)
    admin_headers = {"Authorization": f"Bearer {resp.json()['token']}"}

    # 2. 管理员创建用户
    username = f"e2e_{time.strftime('%H%M%S')}"
    resp = requests.post(
        f"{BASE}/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "password": "secret123",
            "display_name": "E2E 用户",
            "role": "employee",
        },
        timeout=10,
    )
    _check("admin 创建用户", resp.status_code == 201, username)

    # 3. 新用户登录并核对角色
    resp = requests.post(
        f"{BASE}/api/auth/login",
        json={"username": username, "password": "secret123"},
        timeout=10,
    )
    _check("新用户登录", resp.status_code == 200)
    user_headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    me = requests.get(f"{BASE}/api/auth/me", headers=user_headers, timeout=10).json()
    _check("me 返回正确角色", me["role"] == "employee", me["username"])

    # 4. 上传文档
    file_path = Path(args.file)
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{BASE}/api/upload",
            headers=admin_headers,
            files={"file": (file_path.name, f, "text/markdown")},
            data={"domain": args.domain},
            timeout=30,
        )
    _check("上传文档返回 202", resp.status_code == 202, resp.text)
    doc_id = resp.json()["doc_id"]

    # 5. 轮询灌库完成
    status = "pending"
    deadline = time.time() + 180
    while time.time() < deadline:
        resp = requests.get(
            f"{BASE}/api/docs/{doc_id}/status", headers=admin_headers, timeout=10
        )
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(1)
    _check("灌库完成", status == "completed", f"doc_id={doc_id} status={status}")

    # 6. 非流式问答
    question = "公司的核心价值观是什么？"
    resp = requests.post(
        f"{BASE}/api/chat",
        headers=admin_headers,
        json={"question": question, "stream": False},
        timeout=120,
    )
    answer = resp.json().get("answer", "")
    _check("非流式问答返回答案", resp.status_code == 200 and bool(answer), answer[:60])
    sources = resp.json()["sources"]
    _check(
        "引用来源完整",
        bool(sources) and all(s.get("filename") and s.get("domain") for s in sources),
    )

    # 7. SSE 流式问答
    resp = requests.post(
        f"{BASE}/api/chat",
        headers=admin_headers,
        json={"question": question, "stream": True},
        timeout=180,
        stream=True,
    )
    token_count = 0
    done_sources = None
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        event = json.loads(raw[6:])
        if "token" in event:
            token_count += 1
        if event.get("done"):
            done_sources = event["sources"]
    _check(
        "SSE 流式逐 token 输出",
        token_count > 0 and done_sources is not None,
        f"tokens={token_count} sources={len(done_sources or [])}",
    )

    # 8. 越权过滤：employee 问财务问题
    resp = requests.post(
        f"{BASE}/api/chat",
        headers=user_headers,
        json={"question": "2025年净利润是多少？", "stream": False},
        timeout=120,
    )
    domains = {source["domain"] for source in resp.json()["sources"]}
    _check("employee 越权过滤", "finance" not in domains, str(domains))

    # 9. 删除文档并核对
    resp = requests.delete(
        f"{BASE}/api/docs/{doc_id}", headers=admin_headers, timeout=10
    )
    _check("删除文档", resp.status_code == 200, resp.text)
    resp = requests.get(f"{BASE}/api/docs", headers=admin_headers, timeout=10)
    _check(
        "删除后列表不含该文档",
        all(doc["id"] != doc_id for doc in resp.json()),
    )

    print("\n本地端到端联调全部通过。")


if __name__ == "__main__":
    main()
