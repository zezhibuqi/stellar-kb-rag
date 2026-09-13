"use client";

import { MenuOutlined, SendOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Drawer, Empty, Grid, Input, Typography, message } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ConversationList from "@/components/ConversationList";
import SourceCard from "@/components/SourceCard";
import {
  ApiRequestError,
  chatStream,
  createConversation,
  listConversationMessages,
  type ChatSource,
  type StoredMessage,
} from "@/lib/api";

const STICK_THRESHOLD = 120; // 距底小于该像素值时视为「贴底」，流式输出自动跟随

interface ChatItem {
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
}

/** 回放历史消息时把非正常结束的状态显式标出来 */
function replayContent(item: StoredMessage): string {
  if (item.role !== "assistant") return item.content;
  const suffix =
    item.status === "aborted"
      ? "（已停止）"
      : item.status === "failed"
        ? "（生成失败）"
        : item.status === "streaming"
          ? "（未完成）"
          : "";
  if (!suffix) return item.content;
  return item.content ? `${item.content}\n\n${suffix}` : suffix;
}

export default function ChatBox() {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [listToken, setListToken] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const screens = Grid.useBreakpoint();
  const isNarrow = screens.lg === false;

  // 流式输出时若用户处于贴底位置则自动滚动跟随；用户上翻查看历史时不打扰。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
    const raf = requestAnimationFrame(() => {
      if (stickToBottom.current) el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(raf);
  }, [items]);

  const openConversation = useCallback(async (id: number) => {
    setDrawerOpen(false);
    setConversationId(id);
    setItems([]);
    try {
      const stored = await listConversationMessages(id);
      setItems(
        stored.map((item) => ({
          role: item.role,
          content: replayContent(item),
          sources: item.sources,
        }))
      );
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载会话失败"
      );
    }
  }, []);

  const startConversation = async () => {
    try {
      const created = await createConversation();
      setListToken((token) => token + 1);
      await openConversation(created.id);
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "新建会话失败"
      );
    }
  };

  const send = async () => {
    const question = input.trim();
    if (!question || loading || conversationId === null) return;

    setItems((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "" },
    ]);
    setInput("");
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;
    let answer = "";

    const updateLast = (patch: (item: ChatItem) => ChatItem) =>
      setItems((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") next[next.length - 1] = patch(last);
        return next;
      });

    const finishWith = (text: string) => {
      answer = answer ? `${answer}\n\n（${text}）` : text;
      updateLast((item) => ({ ...item, content: answer }));
    };

    try {
      await chatStream(
        conversationId,
        question,
        {
          onToken: (token) => {
            answer += token;
            updateLast((item) => ({ ...item, content: answer }));
          },
          onDone: (sources) => updateLast((item) => ({ ...item, sources })),
          onError: (text) => {
            finishWith(text);
            message.error(text);
          },
        },
        controller.signal
      );
    } catch (error) {
      const aborted = (error as Error).name === "AbortError";
      finishWith(aborted ? "已停止" : "回答失败，请稍后重试");
      if (!aborted) {
        message.error(
          error instanceof ApiRequestError ? error.message : "请求失败，请稍后重试"
        );
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
      // 标题与排序会随新消息变化，刷新会话列表
      setListToken((token) => token + 1);
    }
  };

  const sidebar = (
    <ConversationList
      activeId={conversationId}
      onSelect={openConversation}
      refreshToken={listToken}
    />
  );

  return (
    <div style={{ display: "flex", height: "100%", width: "100%" }}>
      {!isNarrow && (
        <div
          style={{
            width: 240,
            flex: "none",
            height: "100%",
            borderRight: "1px solid var(--app-card-border)",
          }}
        >
          {sidebar}
        </div>
      )}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          maxWidth: 860,
          margin: "0 auto",
          padding: "0 16px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {isNarrow && (
          <div style={{ flex: "none", paddingTop: 10 }}>
            <Button
              size="small"
              icon={<MenuOutlined />}
              onClick={() => setDrawerOpen(true)}
            >
              会话
            </Button>
          </div>
        )}
        <div
          ref={scrollRef}
          onScroll={(event) => {
            const el = event.currentTarget;
            stickToBottom.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
          }}
          style={{ flex: 1, minHeight: 0, overflowY: "auto" }}
        >
          {items.length === 0 ? (
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
                    {conversationId === null
                      ? "先新建一个会话，再向知识库提问"
                      : "向知识库提问，获取带引用来源的回答"}
                  </Typography.Text>
                }
              >
                {conversationId === null && (
                  <Button type="primary" onClick={startConversation}>
                    新建会话
                  </Button>
                )}
              </Empty>
            </div>
          ) : (
            <div style={{ padding: "12px 0 24px" }}>
              {items.map((item, index) => (
                <div
                  key={index}
                  style={
                    item.role === "user"
                      ? { textAlign: "right", margin: "20px 0" }
                      : { margin: "20px 0" }
                  }
                >
                  {item.role === "user" ? (
                    <div className="chat-user-bubble">{item.content}</div>
                  ) : (
                    <>
                      <div className="markdown-preview">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {item.content}
                        </ReactMarkdown>
                      </div>
                      {item.sources && item.sources.length > 0 && (
                        <SourceCard sources={item.sources} />
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ flex: "none", padding: "12px 0 8px" }}>
          <div className="chat-input-card">
            <Input.TextArea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={
                conversationId === null ? "请先新建会话" : "输入问题，Enter 发送"
              }
              autoSize={{ minRows: 1, maxRows: 6 }}
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              disabled={loading || conversationId === null}
              variant="borderless"
            />
            <Button
              type={loading ? "default" : "primary"}
              shape="circle"
              icon={loading ? <StopOutlined /> : <SendOutlined />}
              onClick={loading ? () => abortRef.current?.abort() : send}
              disabled={!loading && conversationId === null}
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
      <Drawer
        title="会话"
        placement="left"
        width={260}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        styles={{ body: { padding: 0 } }}
      >
        {sidebar}
      </Drawer>
    </div>
  );
}
