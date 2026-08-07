"use client";

import { Typography } from "antd";
import ChatBox from "@/components/ChatBox";
import LayoutWrapper from "@/components/LayoutWrapper";

export default function ChatPage() {
  return (
    <LayoutWrapper>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        知识问答
      </Typography.Title>
      <ChatBox />
    </LayoutWrapper>
  );
}
