"use client";

import { Card, Typography } from "antd";

export default function LoginPage() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f5f5f5",
      }}
    >
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ textAlign: "center" }}>
          星辰科技集团 · 知识问答
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center" }}>
          登录页将在 Stage 2 实现
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
