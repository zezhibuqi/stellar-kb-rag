"use client";

import { Empty, Typography } from "antd";

export default function ChatPage() {
  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={3}>知识问答</Typography.Title>
      <Empty description="问答页将在 Stage 6 实现" />
    </div>
  );
}
