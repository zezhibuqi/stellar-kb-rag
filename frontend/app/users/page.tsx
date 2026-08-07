"use client";

import { Empty, Typography } from "antd";

export default function UsersPage() {
  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={3}>用户管理</Typography.Title>
      <Empty description="用户管理页将在 Stage 2 实现" />
    </div>
  );
}
