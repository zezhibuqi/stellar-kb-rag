"use client";

import { MenuOutlined, SendOutlined, StopOutlined } from "@ant-design/icons";
import {
  Button,
  Drawer,
  Empty,
  Grid,
  Input,
  Segmented,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ConversationList from "@/components/ConversationList";
import AnalysisPanel, { type SubProcess } from "@/components/AnalysisPanel";
import SourceCard from "@/components/SourceCard";
import {
  ApiRequestError,
  chatStream,
  createConversation,
  getModelSettings,
  listConversationMessages,
  type ChatSource,
  type ConversationInfo,
  type StoredMessage,
} from "@/lib/api";

const STICK_THRESHOLD = 120; // 距底小于该像素值时视为「贴底」，流式输出自动跟随

interface ChatItem {
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  mode?: string;
  planning?: boolean;
  subProcess?: SubProcess[];
}

const MODE_KEY = "kb-chat-mode";

/** 回放时从消息的 trace 还原分析过程 */
function traceProcess(item: StoredMessage): SubProcess[] {
  const trace = item.trace as
    | { sub_questions?: Array<Record<string, unknown>> }
    | null;
  if (!trace?.sub_questions) return [];
  return trace.sub_questions.map((entry) => ({
    id: Number(entry.id ?? 0),
    text: String(entry.query ?? ""),
    answer: entry.answer ? String(entry.answer) : undefined,
    coverage: entry.coverage ? String(entry.coverage) : undefined,
    error: entry.error ? String(entry.error) : null,
  }));
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
  const [mode, setMode] = useState("standard");
  const [agentCapable, setAgentCapable] = useState<boolean | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const screens = Grid.useBreakpoint();
  const isNarrow = screens.lg === false;

  // 模式选择记在本地；增强模式是否可用取决于当前模型。非管理员查不到模型设置，
  // 此时按「可用」处理，由服务端的 400 校验兜底并给出提示。
  useEffect(() => {
    const stored = window.localStorage.getItem(MODE_KEY);
    if (stored === "enhanced" || stored === "standard") setMode(stored);
    getModelSettings()
      .then((settings) => {
        const active = settings.providers.find(
          (item) => item.id === settings.active
        );
        setAgentCapable(Boolean(active?.agent_capable));
      })
      .catch(() => setAgentCapable(null));
  }, []);

  const changeMode = (value: string) => {
    setMode(value);
    window.localStorage.setItem(MODE_KEY, value);
  };

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
          mode: item.mode,
          subProcess: traceProcess(item),
        }))
      );
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载会话失败"
      );
    }
  }, []);

  const autoSelected = useRef(false);

  /** 首次加载会话列表时自动选中最近更新的那个 */
  const handleLoaded = useCallback(
    (list: ConversationInfo[]) => {
      if (autoSelected.current) return;
      autoSelected.current = true;
      if (list.length > 0) void openConversation(list[0].id);
    },
    [openConversation]
  );

  const handleDeleted = (id: number) => {
    if (conversationId !== id) return;
    setConversationId(null);
    setItems([]);
  };

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
        mode,
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
          onStage: (event) => {
            const stage = String(event.stage);
            if (stage === "planning") {
              updateLast((item) => ({ ...item, planning: true }));
              return;
            }
            if (stage === "planned") {
              const rows = Array.isArray(event.sub_questions)
                ? (event.sub_questions as Array<Record<string, unknown>>)
                : [];
              updateLast((item) => ({
                ...item,
                planning: false,
                subProcess: rows.map((row) => ({
                  id: Number(row.id ?? 0),
                  text: String(row.text ?? ""),
                  dependsOn: (row.depends_on as number | null) ?? null,
                })),
              }));
              return;
            }
            if (stage === "sub_answer") {
              const id = Number(event.sub_question_id ?? 0);
              const patch: SubProcess = {
                id,
                text: "",
                answer: String(event.answer ?? ""),
                coverage: event.coverage ? String(event.coverage) : undefined,
                error: event.error ? String(event.error) : null,
                followUp: Boolean(event.follow_up),
              };
              updateLast((item) => {
                const list = [...(item.subProcess ?? [])];
                const index = list.findIndex((row) => row.id === id);
                if (index >= 0) {
                  list[index] = {
                    ...list[index],
                    ...patch,
                    text: list[index].text || patch.text,
                  };
                } else {
                  list.push(patch);
                }
                return { ...item, subProcess: list };
              });
              return;
            }
            if (stage === "synthesizing") {
              updateLast((item) => ({ ...item, planning: false }));
            }
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
      onLoaded={handleLoaded}
      onDeleted={handleDeleted}
      refreshToken={listToken}
    />
  );

  return (
    <div style={{ display: "flex", height: "100%", width: "100%" }}>
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
          <div
            style={{
              flex: "none",
              paddingTop: 10,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
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
                      <AnalysisPanel
                        subQuestions={item.subProcess ?? []}
                        planning={item.planning}
                        collapsed={item.content.length > 0}
                      />
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
          <div style={{ marginBottom: 8 }}>
            <Tooltip
              title={
                agentCapable === false
                  ? "当前模型不支持增强模式，请让管理员切换到 DeepSeek 系模型"
                  : ""
              }
            >
              <Segmented
                size="small"
                value={mode}
                onChange={(value) => changeMode(String(value))}
                options={[
                  { label: "标准模式", value: "standard" },
                  {
                    label: "增强模式",
                    value: "enhanced",
                    disabled: agentCapable === false,
                  },
                ]}
              />
            </Tooltip>
          </div>
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
      {!isNarrow && (
        <div
          style={{
            width: 240,
            flex: "none",
            height: "100%",
            borderLeft: "1px solid var(--app-card-border)",
          }}
        >
          {sidebar}
        </div>
      )}
      <Drawer
        title="会话"
        placement="right"
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
