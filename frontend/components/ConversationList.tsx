"use client";

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

const CONVERSATION_LIMIT = 5;

interface Props {
  activeId: number | null;
  onSelect: (id: number) => void;
  /** 外部触发刷新（例如回答结束后标题与排序变化） */
  refreshToken?: number;
}

export default function ConversationList({ activeId, onSelect, refreshToken }: Props) {
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setConversations(await listConversations());
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载会话失败"
      );
    } finally {
      setLoading(false);
    }
  }, []);

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
