"use client";

import { Empty, Typography } from "antd";

export default function KnowledgePage() {
  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={3}>知识库管理</Typography.Title>
      <Empty description="知识库管理页将在 Stage 3 实现" />
    </div>
  );
}
