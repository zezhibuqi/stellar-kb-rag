"use client";

import { Card, Tag, Typography } from "antd";
import type { ChatSource } from "@/lib/api";

export default function SourceCard({ sources }: { sources: ChatSource[] }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <Typography.Text strong>引用来源</Typography.Text>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
        {sources.map((source, index) => (
          <Card key={index} size="small">
            <Tag color="blue">{source.domain}</Tag>
            <Typography.Text strong>{source.filename}</Typography.Text>
            <Typography.Paragraph
              type="secondary"
              style={{ marginTop: 4, marginBottom: 0, fontSize: 12 }}
            >
              {source.content_preview}
            </Typography.Paragraph>
          </Card>
        ))}
      </div>
    </div>
  );
}
