"""问答流式接口错误事件测试：生成中途失败应返回 error 事件而非静默断流。"""

import json

import pytest

import chat_api
from app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, username: str, password: str = "123456"):
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def test_stream_emits_error_event_on_failure(client, monkeypatch):
    def fake_answer(question, history=None, user_role="employee", stream=False):
        def generate():
            yield json.dumps({"token": "部分回答"}, ensure_ascii=False)
            raise RuntimeError("模型服务中断")

        return generate()

    monkeypatch.setattr(chat_api, "answer_question", fake_answer)
    token = _login(client, "employee")
    resp = client.post(
        "/api/chat",
        json={"question": "测试问题", "stream": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data: {"token": "部分回答"}' in body
    assert "data: " + json.dumps({"error": "生成失败，请稍后重试"}, ensure_ascii=False) in body
