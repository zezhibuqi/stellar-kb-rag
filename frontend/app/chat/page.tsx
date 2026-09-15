"use client";

/**
 * 问答页：整体布局（顶栏 + 侧边导航）包住聊天区。
 * 会话列表、模式切换、SSE 消费与来源展示都在 ChatBox 内部完成。
 */
import ChatBox from "@/components/ChatBox";
import LayoutWrapper from "@/components/LayoutWrapper";

/** 页面组件：仅负责把 ChatBox 放进全局布局，不持有业务状态。 */
export default function ChatPage() {
  return (
    <LayoutWrapper>
      <ChatBox />
    </LayoutWrapper>
  );
}
