"use client";

/**
 * 主题上下文：亮色 / 暗色两套 antd 主题 token，并负责把选择持久化到 localStorage。
 *
 * 约定：主题只影响 antd 组件 token 与 `data-theme`（CSS 变量按它切换），
 * 不跟随操作系统偏好——用户选过一次就以选择为准。
 */
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ConfigProvider, theme as antdTheme } from "antd";

/** 主题模式：亮色 / 暗色。 */
export type ThemeMode = "light" | "dark";

const FONT_FAMILY =
  "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif";

/** 主题上下文：mode 为当前模式，toggle 在两种模式间切换。 */
const ThemeContext = createContext<{ mode: ThemeMode; toggle: () => void }>({
  mode: "light",
  toggle: () => {},
});

/** 读取当前主题模式的 Hook（页面顶栏与登录页的切换按钮使用）。 */
export function useThemeMode() {
  return useContext(ThemeContext);
}

/**
 * 亮色：白与浅灰的层次 + 默认蓝（#1677ff）仅做点睛。
 * 暗色：参考 VS Code Dark Modern——近黑分层（#131313/#181818/#1f1f1f）
 * + 亮蓝（#3794ff）辅助；body 背景固定，不跟随操作系统。
 */
/** 亮色主题 token（与暗色共用同一套组件级配置，仅颜色不同）。 */
const lightTheme = {
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
    fontFamily: FONT_FAMILY,
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

/** 暗色主题：基于 antd darkAlgorithm，再覆写 VS Code Dark Modern 风格的背景层次。 */
const darkTheme = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: "#3794ff",
    colorInfo: "#3794ff",
    colorLink: "#3794ff",
    borderRadius: 8,
    colorBorder: "#3c3c3c",
    colorBorderSecondary: "#2e2e2e",
    colorBgLayout: "#131313",
    colorText: "#cccccc",
    colorTextSecondary: "#9a9a9a",
    colorTextTertiary: "#6e6e6e",
    fontSize: 14,
    fontFamily: FONT_FAMILY,
  },
  components: {
    Layout: {
      headerBg: "#181818",
      headerHeight: 56,
      siderBg: "#181818",
      bodyBg: "#131313",
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: "rgba(55, 148, 255, 0.16)",
      itemSelectedColor: "#3794ff",
      itemBorderRadius: 8,
      itemMarginInline: 12,
      itemHeight: 40,
      activeBarBorderWidth: 0,
    },
    Table: {
      headerBg: "#1c1c1c",
      headerColor: "#9a9a9a",
      rowHoverBg: "#212121",
      headerSplitColor: "transparent",
      borderColor: "#2e2e2e",
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
    Collapse: { contentBg: "transparent" },
  },
};

/** localStorage 键名；与 layout.tsx 里首帧内联脚本里的字面量必须保持一致。 */
const STORAGE_KEY = "kb-theme";

/**
 * 主题提供者：维护模式状态、同步到 localStorage 与 `data-theme`，
 * 并把对应主题交给 antd 的 ConfigProvider。
 */
export default function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("light");

  // 首次挂载读取用户选择（配合 layout 内联脚本，避免暗色用户看到亮色闪烁）
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "dark") setMode("dark");
    } catch {
      // localStorage 不可用时保持默认亮色
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 忽略写入失败
    }
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((current) => (current === "light" ? "dark" : "light"));
  }, []);

  return (
    <ThemeContext.Provider value={{ mode, toggle }}>
      <ConfigProvider theme={mode === "dark" ? darkTheme : lightTheme}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}
