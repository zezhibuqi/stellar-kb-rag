"use client";

import type { ReactNode } from "react";
import { ConfigProvider } from "antd";

/**
 * 全局主题令牌：白与浅灰的层次 + 默认蓝仅做点睛。
 * 基调以亮色为准，body 背景固定（不跟随操作系统暗色模式）。
 */
const themeConfig = {
  token: {
    colorPrimary: "#1677ff",
    colorInfo: "#1677ff",
    colorLink: "#1677ff",
    borderRadius: 8,
    colorBorder: "#dfe3e8",
    colorBorderSecondary: "#eceef2",
    colorBgLayout: "#f6f7f9",
    colorText: "#1f2329",
    colorTextSecondary: "#5f6570",
    colorTextTertiary: "#8f959e",
    fontSize: 14,
    fontFamily:
      "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
  },
  components: {
    Layout: {
      headerBg: "#ffffff",
      headerHeight: 56,
      siderBg: "#ffffff",
      bodyBg: "#f6f7f9",
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: "#e8f3ff",
      itemSelectedColor: "#1677ff",
      itemBorderRadius: 8,
      itemMarginInline: 12,
      itemHeight: 40,
      activeBarBorderWidth: 0,
    },
    Table: {
      headerBg: "#fafbfc",
      headerColor: "#5f6570",
      rowHoverBg: "#f7f9fc",
      headerSplitColor: "transparent",
      borderColor: "#eceef2",
      cellPaddingBlock: 13,
    },
    Button: {
      controlHeight: 36,
      borderRadius: 8,
    },
    Input: { controlHeight: 36 },
    Select: { controlHeight: 36 },
    Card: { borderRadiusLG: 12 },
    Modal: { borderRadiusLG: 12 },
    Collapse: { contentBg: "#ffffff" },
  },
};

export default function ThemeProvider({ children }: { children: ReactNode }) {
  return <ConfigProvider theme={themeConfig}>{children}</ConfigProvider>;
}
