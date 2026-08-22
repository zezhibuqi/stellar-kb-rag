"use client";

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Button, Spin, Tag, Typography } from "antd";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { ApiRequestError, getDocRaw, type RawDoc } from "@/lib/api";

function addSourceLinePlugin() {
  return (tree: any) => {
    const visit = (node: any) => {
      if (node.position?.start?.line) {
        node.properties = node.properties ?? {};
        node.properties["data-source-line"] = node.position.start.line;
      }
      if (node.children) {
        node.children.forEach(visit);
      }
    };
    visit(tree);
    return tree;
  };
}

export default function ViewerPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<RawDoc | null>(null);
  const [params, setParams] = useState<URLSearchParams | null>(null);
  const [located, setLocated] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setParams(new URLSearchParams(window.location.search));
  }, []);

  useEffect(() => {
    if (!params) return;
    const docId = Number(params.get("doc_id"));
    if (!docId) {
      setError("缺少 doc_id 参数");
      setLoading(false);
      return;
    }
    getDocRaw(docId)
      .then((data) => {
        setRaw(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof ApiRequestError ? err.message : "加载原文档失败");
        setLoading(false);
      });
  }, [params]);

  useEffect(() => {
    if (!raw || !params || located) return;
    const startLine = Number(params.get("start_line"));
    if (!startLine) {
      setLocated(true);
      return;
    }

    const timer = setTimeout(() => {
      const elements = Array.from(
        document.querySelectorAll<HTMLElement>("[data-source-line]")
      );
      let target =
        elements.find(
          (element) => Number(element.dataset.sourceLine) === startLine
        ) ?? null;
      if (!target) {
        const previous = elements.filter(
          (element) => Number(element.dataset.sourceLine) < startLine
        );
        if (previous.length > 0) {
          target = previous[previous.length - 1];
        }
      }
      if (target) {
        highlight(target);
      } else {
        const preview = params.get("preview") ?? "";
        // 原文档可能含 HTML 表格，定位文本需去掉标签后匹配
        const needle = preview.replace(/<[^>]*>/g, "").trim().slice(0, 40);
        if (needle) {
          const root = document.querySelector(".viewer-markdown");
          if (root) {
            const walker = document.createTreeWalker(
              root,
              NodeFilter.SHOW_TEXT
            );
            let node: Node | null;
            while ((node = walker.nextNode())) {
              if (node.textContent && node.textContent.includes(needle)) {
                const parent = node.parentElement;
                if (parent) {
                  highlight(parent);
                }
                break;
              }
            }
          }
        }
      }
      setLocated(true);
    }, 100);
    return () => clearTimeout(timer);
  }, [raw, params, located]);

  const highlight = (element: HTMLElement) => {
    element.scrollIntoView({ behavior: "smooth", block: "start" });
    element.classList.add("source-highlight");
    setTimeout(() => element.classList.remove("source-highlight"), 2200);
  };

  // 纯浏览器交互页面：挂载完成前不渲染，避免 hydration 不一致
  if (!mounted) {
    return null;
  }

  if (loading) {
    return (
      <div style={{ padding: 96, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !raw) {
    return (
      <div style={{ maxWidth: 720, margin: "0 auto", padding: "40px 24px" }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => router.push("/chat")}>
          返回问答
        </Button>
        <Typography.Paragraph type="danger" style={{ marginTop: 16 }}>
          {error ?? "加载失败"}
        </Typography.Paragraph>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: "#f6f7f9" }}>
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "10px 24px",
          background: "#ffffff",
          borderBottom: "1px solid #f0f1f4",
        }}
      >
        <Button
          type="text"
          size="small"
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push("/chat")}
        >
          返回问答
        </Button>
        <Typography.Text strong ellipsis style={{ flex: 1 }}>
          {raw.filename}
        </Typography.Text>
        <Tag>{raw.domain}</Tag>
      </div>
      <div style={{ maxWidth: 860, margin: "0 auto", padding: "32px 24px 64px" }}>
        <div className="app-card" style={{ padding: "40px 48px" }}>
          <div className="viewer-markdown markdown-preview">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeRaw, addSourceLinePlugin as any]}
            >
              {raw.content}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
