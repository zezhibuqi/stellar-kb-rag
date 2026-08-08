"use client";

import { ArrowLeftOutlined } from "@ant-design/icons";
import { Button, Card, Spin, Tag, Typography } from "antd";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
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
      const target = document.querySelector(
        `[data-source-line="${startLine}"]`
      ) as HTMLElement | null;
      if (target) {
        highlight(target);
      } else {
        const preview = params.get("preview") ?? "";
        const needle = preview.trim().slice(0, 40);
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
      <div style={{ padding: 48, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !raw) {
    return (
      <div style={{ padding: 24 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => router.push("/chat")}>
          返回问答
        </Button>
        <Typography.Paragraph type="danger" style={{ marginTop: 16 }}>
          {error ?? "加载失败"}
        </Typography.Paragraph>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => router.push("/chat")}
        style={{ marginBottom: 16 }}
      >
        返回问答
      </Button>
      <Card>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          {raw.filename}
        </Typography.Title>
        <Tag color="blue">{raw.domain}</Tag>
        <div className="viewer-markdown markdown-preview">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[addSourceLinePlugin as any]}
          >
            {raw.content}
          </ReactMarkdown>
        </div>
      </Card>
    </div>
  );
}
