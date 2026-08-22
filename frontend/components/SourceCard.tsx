"use client";

import { DatabaseOutlined, FileSearchOutlined } from "@ant-design/icons";
import { Typography } from "antd";
import type { ChatSource } from "@/lib/api";

const DOMAIN_LABELS: Record<string, string> = {
  finance: "财务数据",
  regulation: "规章制度",
  product: "产品规格",
  aftersale: "售后政策",
  common: "公共知识",
};

export default function SourceCard({ sources }: { sources: ChatSource[] }) {
  return (
    <div
      className="app-card"
      style={{ marginTop: 20, padding: "6px 10px" }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px 6px",
        }}
      >
        <FileSearchOutlined style={{ color: "#8f959e", fontSize: 13 }} />
        <Typography.Text strong style={{ fontSize: 13 }}>
          引用来源 · {sources.length} 条
        </Typography.Text>
      </div>
      {sources.map((source, index) => {
        const isDatabase = source.source_type === "database";
        const viewerUrl =
          source.doc_id != null
            ? `/viewer?doc_id=${source.doc_id}&start_line=${source.start_line}&preview=${encodeURIComponent(
                source.content_preview.slice(0, 60)
              )}`
            : null;
        return (
          <div className="source-row" key={index}>
            <span className={`source-icon${isDatabase ? " database" : ""}`}>
              {isDatabase ? <DatabaseOutlined /> : <FileSearchOutlined />}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <Typography.Text strong style={{ fontSize: 13 }}>
                  {source.filename}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {isDatabase ? "数据库" : DOMAIN_LABELS[source.domain] ?? source.domain}
                </Typography.Text>
              </div>
              <div className="source-preview">{source.content_preview}</div>
            </div>
            {viewerUrl && (
              <Typography.Link
                href={viewerUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{ flex: "none", fontSize: 12.5 }}
              >
                查看原文
              </Typography.Link>
            )}
          </div>
        );
      })}
    </div>
  );
}
