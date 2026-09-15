r"""增强模式演示脚本（设计与开发文档 2.6 / 7.8）。

用法（在项目根目录执行，需先启动后端并确保当前模型支持增强模式）：

    .\.venv\Scripts\python.exe backend\demo_enhanced.py
    .\.venv\Scripts\python.exe backend\demo_enhanced.py --mode standard

脚本会：登录 → 新建会话 → 以指定模式提问 → 打印 stage 事件、最终回答与来源。
"""

import argparse
import json

import requests

BASE = "http://localhost:5000"

DEFAULT_QUESTION = (
    "为 SC-500 工商业储能一体柜供电的那款电芯，"
    "它的单体质量能量密度和 25℃ 循环寿命分别是多少？"
)


def _login(username: str, password: str) -> str:
    """演示脚本登录，返回 JWT；非 2xx 直接抛异常终止演示。"""
    resp = requests.post(
        f"{BASE}/api/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def main() -> None:
    """增强模式端到端演示：登录 → 新建会话 → 提问 → 打印 stage 事件/回答/来源。

    这是人工验收增强模式的主要入口（交接文档第 5 节）：
    观察 planned 的子问题列表、sub_answer 的覆盖状态、补充检索标记与分组来源。
    """
    parser = argparse.ArgumentParser(description="增强模式演示")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="123456")
    parser.add_argument(
        "--mode", default="enhanced", choices=["enhanced", "standard"]
    )
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {_login(args.username, args.password)}"}
    created = requests.post(
        f"{BASE}/api/conversations", headers=headers, timeout=10
    )
    if created.status_code not in (200, 201):
        print(f"新建会话失败：{created.status_code} {created.text}")
        print("提示：每用户最多保留 5 个会话（CONVERSATION_LIMIT），先删一个再跑。")
        return
    conversation_id = created.json()["id"]

    print(f"会话 id={conversation_id}｜模式={args.mode}")
    print(f"问题：{args.question}\n")

    answer = ""
    with requests.post(
        f"{BASE}/api/chat",
        headers=headers,
        json={
            "conversation_id": conversation_id,
            "question": args.question,
            "mode": args.mode,
            "stream": True,
        },
        timeout=300,
        stream=True,
    ) as resp:
        if resp.status_code != 200:
            print(f"请求失败：{resp.status_code} {resp.text}")
            return
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            event = json.loads(raw[6:])
            if event.get("stage") == "planned":
                print("规划完成，子问题：")
                for item in event["sub_questions"]:
                    print(f"  {item['id']}. {item['text']}")
            elif event.get("stage") == "sub_answer":
                tag = "补充检索 " if event.get("follow_up") else ""
                detail = (
                    f"［{event['coverage']}］" if event.get("coverage") else ""
                )
                if event.get("error"):
                    detail += f"（失败：{event['error']}）"
                print(
                    f"{tag}子答案 {event['sub_question_id']}"
                    f"{detail}：{event['answer']}"
                )
            elif event.get("stage"):
                print(f"阶段：{event['stage']}")
            elif "token" in event:
                answer += event["token"]
            elif event.get("done"):
                sources = event.get("sources") or []
                print(f"\n最终回答：\n{answer}\n")
                print(f"来源 {len(sources)} 条：")
                for source in sources:
                    print(
                        f"  - 子问题 {source.get('sub_question_id')}｜"
                        f"{source.get('filename')}（{source.get('domain')}）"
                    )
            elif "error" in event:
                print(f"错误事件：{event['error']}")

    print(
        f"\n过程记录已随消息落库，可查看 "
        f"{BASE}/api/conversations/{conversation_id}/messages"
    )


if __name__ == "__main__":
    main()
