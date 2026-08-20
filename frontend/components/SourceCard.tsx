"use client";

import { EyeOutlined } from "@ant-design/icons";
import { Button, Card, Tag, Typography } from "antd";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import type { ChatSource } from "@/lib/api";

export default function SourceCard({ sources }: { sources: ChatSource[] }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <Typography.Text strong>引用来源</Typography.Text>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          marginTop: 8,
        }}
      >
        {sources.map((source, index) => {
          const viewerUrl = `/viewer?doc_id=${source.doc_id}&start_line=${source.start_line}&preview=${encodeURIComponent(
            source.content_preview.slice(0, 60)
          )}`;
          return (
            <Card key={index} size="small">
              <div style={{ marginBottom: 8 }}>
                <Tag color={source.source_type === "database" ? "green" : "blue"}>
                  {source.source_type === "database" ? "数据库" : source.domain}
                </Tag>
                <Typography.Text strong>{source.filename}</Typography.Text>
              </div>
              <div className="markdown-preview">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeRaw]}
                >
                  {source.content_preview}
                </ReactMarkdown>
              </div>
              {source.doc_id != null && (
                <Button
                  type="link"
                  size="small"
                  icon={<EyeOutlined />}
                  href={viewerUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ paddingLeft: 0, marginTop: 4 }}
                >
                  查看原文档
                </Button>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
