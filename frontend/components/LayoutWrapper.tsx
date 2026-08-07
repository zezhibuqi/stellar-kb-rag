"use client";

import type { ReactNode } from "react";

export default function LayoutWrapper({ children }: { children: ReactNode }) {
  // Stage 2 实现：Header + 侧边栏 + 路由守卫
  return <>{children}</>;
}
