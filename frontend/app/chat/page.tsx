"use client";

import { Empty, Typography } from "antd";
import LayoutWrapper from "@/components/LayoutWrapper";

export default function ChatPage() {
  return (
    <LayoutWrapper>
      <Typography.Title level={3}>知识问答</Typography.Title>
      <Empty description="问答页将在 Stage 6 实现" />
    </LayoutWrapper>
  );
}
