"use client";

import { SendOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Empty, Input, Typography, message } from "antd";
import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SourceCard from "@/components/SourceCard";
import {
  ApiRequestError,
  chatStream,
  type ChatMessage,
  type ChatSource,
} from "@/lib/api";

const HISTORY_LIMIT = 20; // 最近 10 轮（20 条消息）

export default function ChatBox() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sources, setSources] = useState<ChatSource[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const stop = () => {
    abortRef.current?.abort();
  };

  const send = async () => {
    const question = input.trim();
    if (!question || loading) return;

    const historyForApi = messages.slice(-HISTORY_LIMIT);
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setInput("");
    setSources([]);
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;
    let answer = "";

    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);
    const markFailed = (text: string) => {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = {
            role: "assistant",
            content: last.content
              ? `${last.content}\n\n（${text}）`
              : text,
          };
        }
        return next;
      });
      message.error(text);
    };
    try {
      await chatStream(
        question,
        historyForApi,
        (token) => {
          answer += token;
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", content: answer };
            return next;
          });
        },
        (doneSources) => setSources(doneSources),
        controller.signal,
        (errorText) => markFailed(errorText)
      );
    } catch (error) {
      const aborted = (error as Error).name === "AbortError";
      if (aborted) {
        markFailed("已停止生成");
      } else {
        markFailed("回答失败，请稍后重试");
        message.error(
          error instanceof ApiRequestError ? error.message : "请求失败，请稍后重试"
        );
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      {messages.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Typography.Text type="secondary">
              向知识库提问，获取带引用来源的回答
            </Typography.Text>
          }
          style={{ margin: "72px 0" }}
        />
      ) : (
        <div>
          {messages.map((msg, index) => (
            <div
              key={index}
              style={
                msg.role === "user"
                  ? { textAlign: "right", margin: "20px 0" }
                  : { margin: "20px 0" }
              }
            >
              {msg.role === "user" ? (
                <div className="chat-user-bubble">{msg.content}</div>
              ) : (
                <div className="markdown-preview">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {sources.length > 0 && <SourceCard sources={sources} />}

      <div style={{ marginTop: 20 }}>
        <div className="chat-input-card">
          <Input.TextArea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="输入问题，Enter 发送"
            autoSize={{ minRows: 1, maxRows: 6 }}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
            disabled={loading}
            variant="borderless"
          />
          <Button
            type={loading ? "default" : "primary"}
            shape="circle"
            icon={loading ? <StopOutlined /> : <SendOutlined />}
            onClick={loading ? stop : send}
            style={{ flex: "none", width: 38, height: 38 }}
          />
        </div>
        <Typography.Paragraph
          type="secondary"
          style={{ marginTop: 10, fontSize: 12, textAlign: "center" }}
        >
          Enter 发送 · Shift+Enter 换行 · 回答基于角色权限内的知识资料与订单数据，请以引用来源为准
        </Typography.Paragraph>
      </div>
    </div>
  );
}
