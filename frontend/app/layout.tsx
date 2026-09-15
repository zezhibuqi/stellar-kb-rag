/**
 * 全局布局（App Router 根布局）：
 * - 注入 Inter 字体变量与 antd 的 SSR 样式注册表（避免首屏样式闪烁）；
 * - 用内联脚本在首帧前应用用户选定的主题（暗色用户不会先看到亮色）；
 * - 页面标题与描述统一在此声明。
 */
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import ThemeProvider from "@/components/ThemeProvider";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

/** 站点级元信息（浏览器标题栏与 SEO 描述）。 */
export const metadata: Metadata = {
  title: "星辰科技集团 · 知识问答系统",
  description: "多领域、多角色的企业知识问答平台（RAG）",
};

/** 根布局：包住 antd 注册表与主题 Provider，所有页面共享。 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={inter.variable}>
      <head>
        {/* 首帧前应用用户选择的主题，避免暗色用户看到亮色闪烁 */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{if(localStorage.getItem('kb-theme')==='dark')document.documentElement.dataset.theme='dark'}catch(e){}",
          }}
        />
      </head>
      <body>
        <AntdRegistry>
          <ThemeProvider>{children}</ThemeProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
