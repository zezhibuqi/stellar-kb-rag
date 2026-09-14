"use client";

import { DownOutlined, RightOutlined } from "@ant-design/icons";
import { Tag, Typography } from "antd";
import { useEffect, useState } from "react";

export interface SubProcess {
  id: number;
  text: string;
  answer?: string;
  coverage?: string;
  error?: string | null;
  followUp?: boolean;
  dependsOn?: number | null;
}

const COVERAGE_LABEL: Record<string, { text: string; color: string }> = {
  sufficient: { text: "证据充分", color: "green" },
  partial: { text: "部分缺失", color: "orange" },
  missing: { text: "证据缺失", color: "red" },
};

interface Props {
  subQuestions: SubProcess[];
  /** 回答开始流式输出后收起为一行摘要 */
  collapsed: boolean;
  planning?: boolean;
}

export default function AnalysisPanel({ subQuestions, collapsed, planning }: Props) {
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (collapsed) setOpen(false);
  }, [collapsed]);

  if (!planning && subQuestions.length === 0) return null;

  const summary = planning
    ? "正在分析问题…"
    : `本次拆解为 ${subQuestions.length} 个子问题`;

  return (
    <div
      style={{
        border: "1px solid var(--app-card-border)",
        borderRadius: 10,
        padding: "8px 12px",
        marginBottom: 12,
        background: "rgba(22, 119, 255, 0.04)",
      }}
    >
      <div
        onClick={() => setOpen((value) => !value)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          cursor: subQuestions.length ? "pointer" : "default",
        }}
      >
        {subQuestions.length > 0 &&
          (open ? <DownOutlined /> : <RightOutlined />)}
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {summary}
        </Typography.Text>
      </div>
      {open && subQuestions.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {subQuestions.map((item) => {
            const coverage = item.coverage
              ? COVERAGE_LABEL[item.coverage] ?? {
                  text: item.coverage,
                  color: "default",
                }
              : null;
            return (
              <div key={item.id} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <Typography.Text style={{ fontSize: 13 }}>
                    {item.followUp ? "补充检索" : "子问题"} {item.id}：{item.text}
                  </Typography.Text>
                  {coverage && <Tag color={coverage.color}>{coverage.text}</Tag>}
                </div>
                {item.error ? (
                  <Typography.Text type="danger" style={{ fontSize: 12 }}>
                    失败：{item.error}
                  </Typography.Text>
                ) : item.answer ? (
                  <Typography.Paragraph
                    type="secondary"
                    style={{ fontSize: 12, margin: "2px 0 0" }}
                  >
                    {item.answer}
                  </Typography.Paragraph>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
