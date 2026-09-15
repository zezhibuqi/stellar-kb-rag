"use client";

/**
 * 会话列表（问答页侧边栏 / 窄屏抽屉）：
 * 列表按最近更新倒序，支持新建、切换、二次确认删除，达到上限时禁用新建。
 */
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Empty, List, Popconfirm, Tooltip, Typography, message } from "antd";
import { useCallback, useEffect, useState } from "react";
import {
  ApiRequestError,
  createConversation,
  deleteConversation,
  listConversations,
  type ConversationInfo,
} from "@/lib/api";

/** 每用户会话上限，必须与后端 models.CONVERSATION_LIMIT 保持一致。 */
const CONVERSATION_LIMIT = 5;

interface Props {
  /** 当前选中的会话 id（高亮用；null 表示还没选中）。 */
  activeId: number | null;
  onSelect: (id: number) => void;
  /** 列表加载完成（父组件据此自动选中最近更新的会话） */
  onLoaded?: (conversations: ConversationInfo[]) => void;
  /** 某个会话被删除（父组件据此清掉当前选中） */
  onDeleted?: (id: number) => void;
  /** 外部触发刷新（例如回答结束后标题与排序变化） */
  refreshToken?: number;
}

/**
 * 会话列表组件。
 *
 * 刷新时机有两类：外部 refreshToken 变化（提问结束后标题/排序会变），
 * 以及本组件内部的新建/删除操作。
 */
export default function ConversationList({
  activeId,
  onSelect,
  onLoaded,
  onDeleted,
  refreshToken,
}: Props) {
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listConversations();
      setConversations(list);
      onLoaded?.(list);
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载会话失败"
      );
    } finally {
      setLoading(false);
    }
  }, [onLoaded]);

  useEffect(() => {
    reload();
  }, [reload, refreshToken]);

  const atLimit = conversations.length >= CONVERSATION_LIMIT;

  const create = async () => {
    setCreating(true);
    try {
      const created = await createConversation();
      await reload();
      onSelect(created.id);
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "新建会话失败"
      );
    } finally {
      setCreating(false);
    }
  };

  const remove = async (id: number) => {
    try {
      await deleteConversation(id);
      message.success("会话已删除");
      onDeleted?.(id);
      await reload();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "删除会话失败"
      );
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <div style={{ padding: "12px 12px 8px" }}>
        <Tooltip
          title={atLimit ? `最多保留 ${CONVERSATION_LIMIT} 个会话，请先删除一个` : ""}
        >
          <Button
            type="primary"
            block
            icon={<PlusOutlined />}
            disabled={atLimit}
            loading={creating}
            onClick={create}
          >
            新建会话
          </Button>
        </Tooltip>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0 8px 12px" }}>
        {conversations.length === 0 && !loading ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无会话"
            style={{ marginTop: 24 }}
          />
        ) : (
          <List
            size="small"
            split={false}
            dataSource={conversations}
            renderItem={(item) => (
              <List.Item
                onClick={() => onSelect(item.id)}
                style={{
                  cursor: "pointer",
                  borderRadius: 8,
                  padding: "8px 10px",
                  marginBottom: 4,
                  background:
                    item.id === activeId ? "rgba(22, 119, 255, 0.12)" : undefined,
                }}
                actions={[
                  <Popconfirm
                    key="delete"
                    title="删除该会话？"
                    description="会话及其全部消息将被永久删除。"
                    okText="删除"
                    cancelText="取消"
                    onConfirm={() => remove(item.id)}
                  >
                    <DeleteOutlined
                      style={{ color: "#8c8c8c" }}
                      onClick={(event) => event.stopPropagation()}
                    />
                  </Popconfirm>,
                ]}
              >
                <Typography.Text ellipsis style={{ maxWidth: 140 }}>
                  {item.title || "新会话"}
                </Typography.Text>
              </List.Item>
            )}
          />
        )}
      </div>
    </div>
  );
}
