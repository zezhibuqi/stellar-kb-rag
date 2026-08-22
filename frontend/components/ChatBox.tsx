"use client";

import { SendOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Empty, Input, Typography, message } from "antd";
import { useEffect, useRef, useState } from "react";
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
const STICK_THRESHOLD = 120; // 距底小于该像素值时视为“贴底”，流式输出自动跟随

export default function ChatBox() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sources, setSources] = useState<ChatSource[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const stop = () => {
    abortRef.current?.abort();
  };

  // 流式输出时若用户处于贴底位置则自动滚动跟随；用户上翻查看历史时不打扰。
  // 来源卡片/表格等在首帧后可能继续增高，下一帧补偿滚动一次。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
    const raf = requestAnimationFrame(() => {
      if (stickToBottom.current) el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(raf);
  }, [messages, sources]);

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
    <div
      style={{
        maxWidth: 860,
        width: "100%",
        height: "100%",
        margin: "0 auto",
        padding: "0 16px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        ref={scrollRef}
        onScroll={(event) => {
          const el = event.currentTarget;
          stickToBottom.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
        }}
        style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
      >
        {messages.length === 0 ? (
          <div
            style={{
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Typography.Text type="secondary">
                  向知识库提问，获取带引用来源的回答
                </Typography.Text>
              }
            />
          </div>
        ) : (
          <div style={{ padding: "12px 0 24px" }}>
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
            {sources.length > 0 && <SourceCard sources={sources} />}
          </div>
        )}
      </div>

      <div style={{ flex: "none", padding: "12px 0 8px" }}>
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
          style={{ marginTop: 8, fontSize: 12, textAlign: "center" }}
        >
          Enter 发送 · Shift+Enter 换行 · 回答基于角色权限内的知识资料与订单数据，请以引用来源为准
        </Typography.Paragraph>
      </div>
    </div>
  );
}
