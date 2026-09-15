"use client";

/**
 * 引用来源卡片：默认收起，展开后按来源类型渲染。
 *
 * 增强模式的来源带 sub_question_id，按子问题分组显示；标准模式没有该字段，
 * 保持平铺列表。数据库来源（订单）用不同图标与标签区分，且没有"查看原文"入口。
 */
import { DatabaseOutlined, FileSearchOutlined } from "@ant-design/icons";
import { Collapse, Typography } from "antd";
import { useEffect, useState } from "react";
import type { ChatSource } from "@/lib/api";

/** 领域标识 → 中文展示名（与后端 domains 表的 display_name 对应）。 */
const DOMAIN_LABELS: Record<string, string> = {
  finance: "财务数据",
  regulation: "规章制度",
  product: "产品规格",
  aftersale: "售后政策",
  common: "公共知识",
};

/**
 * 来源卡片。
 *
 * sources 变化（新一轮回答）时重置为收起状态，避免上一轮的展开状态串到下一轮。
 */
export default function SourceCard({ sources }: { sources: ChatSource[] }) {
  // 默认收起；每轮新回答（sources 变化）重置为收起
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setOpen(false);
  }, [sources]);

  // 增强模式的来源带 sub_question_id：按子问题分组展示；
  // 标准模式（无该字段）保持原来的平铺列表。
  // 增强模式的来源带 sub_question_id：按子问题分组展示；
  // 标准模式（无该字段）保持原来的平铺列表。
  const hasSubQuestions = sources.some(
    (source) => source.sub_question_id != null
  );

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
            children: sources.flatMap((source, index) => {
              const previous = index > 0 ? sources[index - 1] : null;
              const showLabel =
                hasSubQuestions &&
                source.sub_question_id !== previous?.sub_question_id;
              const label = (
                <Typography.Text
                  key={`group-${index}`}
                  type="secondary"
                  style={{ fontSize: 12.5, display: "block", margin: "6px 0 2px" }}
                >
                  子问题 {source.sub_question_id ?? "—"}
                </Typography.Text>
              );
              const isDatabase = source.source_type === "database";
              // 只有向量来源能跳原文；数据库来源没有 doc_id，只展示预览
              const viewerUrl =
                source.doc_id != null
                  ? `/viewer?doc_id=${source.doc_id}&start_line=${source.start_line}&preview=${encodeURIComponent(
                      source.content_preview.slice(0, 60)
                    )}`
                  : null;
              const row = (
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
              return showLabel ? [label, row] : [row];
            }),
          },
        ]}
      />
    </div>
  );
}
