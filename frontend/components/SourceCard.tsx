"use client";

import { DatabaseOutlined, FileSearchOutlined } from "@ant-design/icons";
import { Collapse, Typography } from "antd";
import { useEffect, useState } from "react";
import type { ChatSource } from "@/lib/api";

const DOMAIN_LABELS: Record<string, string> = {
  finance: "财务数据",
  regulation: "规章制度",
  product: "产品规格",
  aftersale: "售后政策",
  common: "公共知识",
};

export default function SourceCard({ sources }: { sources: ChatSource[] }) {
  // 默认收起；每轮新回答（sources 变化）重置为收起
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setOpen(false);
  }, [sources]);

  return (
    <div className="app-card source-collapse" style={{ marginTop: 20 }}>
      <Collapse
        ghost
        expandIconPosition="end"
        activeKey={open ? ["sources"] : []}
        onChange={(keys) =>
          setOpen((keys as string[]).includes("sources"))
        }
        items={[
          {
            key: "sources",
            label: (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <FileSearchOutlined style={{ color: "#8f959e", fontSize: 13 }} />
                <Typography.Text strong style={{ fontSize: 13 }}>
                  引用来源 · {sources.length} 条
                </Typography.Text>
                {!open && (
                  <Typography.Text type="secondary" style={{ fontSize: 12.5 }} ellipsis>
                    {sources[0]?.filename ?? ""}
                    {sources.length > 1 ? " 等" : ""}
                  </Typography.Text>
                )}
              </span>
            ),
            children: sources.map((source, index) => {
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
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Typography.Text strong style={{ fontSize: 13 }}>
                        {source.filename}
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {isDatabase
                          ? "数据库"
                          : DOMAIN_LABELS[source.domain] ?? source.domain}
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
            }),
          },
        ]}
      />
    </div>
  );
}
