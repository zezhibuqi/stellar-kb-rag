"use client";

import { SendOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Empty, Input, Space, Typography, message } from "antd";
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
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      {messages.length === 0 ? (
        <Empty description="输入问题开始知识问答" style={{ margin: "48px 0" }} />
      ) : (
        <div style={{ marginBottom: 16 }}>
          {messages.map((msg, index) => (
            <div
              key={index}
              style={{
                textAlign: msg.role === "user" ? "right" : "left",
                margin: "12px 0",
              }}
            >
              <div
                style={{
                  display: "inline-block",
                  maxWidth: "80%",
                  padding: "10px 14px",
                  borderRadius: 8,
                  background: msg.role === "user" ? "#1677ff" : "#f5f5f5",
                  color: msg.role === "user" ? "#fff" : "#000",
                  wordBreak: "break-word",
                }}
              >
                {msg.role === "user" ? (
                  <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span>
                ) : (
                  <div className="markdown-preview">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {sources.length > 0 && <SourceCard sources={sources} />}

      <Space.Compact style={{ width: "100%" }}>
        <Input.TextArea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入问题，Enter 发送（Shift+Enter 换行）"
          autoSize={{ minRows: 1, maxRows: 4 }}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
          disabled={loading}
        />
        <Button
          type="primary"
          icon={loading ? <StopOutlined /> : <SendOutlined />}
          onClick={loading ? stop : send}
        >
          {loading ? "停止" : "发送"}
        </Button>
      </Space.Compact>
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
        回答仅基于角色权限范围内的知识库资料，请以引用来源为准。
      </Typography.Paragraph>
    </div>
  );
}
