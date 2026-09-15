"use client";

/**
 * 增强模式「分析过程」容器：位于回答上方，实时展示子问题与证据覆盖状态。
 *
 * 数据来源有两处：流式期间由 ChatBox 消费 SSE 的 planned / sub_answer 事件累积；
 * 回放时由 ChatBox 从消息 trace 还原。回答开始输出后默认收起为一行摘要。
 */
import { DownOutlined, RightOutlined } from "@ant-design/icons";
import { Tag, Typography } from "antd";
import { useEffect, useState } from "react";

/** 单个子问题的展示数据（流式事件与 trace 回放共用同一结构）。 */
export interface SubProcess {
  id: number;
  text: string;
  answer?: string;
  coverage?: string;
  error?: string | null;
  followUp?: boolean;
  dependsOn?: number | null;
}

/** 证据覆盖状态的中文标签与颜色（与后端 coverage 取值一一对应）。 */
const COVERAGE_LABEL: Record<string, { text: string; color: string }> = {
  sufficient: { text: "证据充分", color: "green" },
  partial: { text: "部分缺失", color: "orange" },
  missing: { text: "证据缺失", color: "red" },
};

interface Props {
  /** 已拆解出的子问题（含补充检索项，按出现顺序排列）。 */
  subQuestions: SubProcess[];
  /** 回答开始流式输出后收起为一行摘要 */
  collapsed: boolean;
  /** 规划阶段：只显示「正在分析问题…」，此时还没有子问题 */
  planning?: boolean;
}

/**
 * 分析过程面板。
 *
 * 无子问题且不在规划中时整体不渲染（标准模式回答上方不会出现任何容器）；
 * 有子问题时可点击标题行手动展开/收起。
 */
export default function AnalysisPanel({ subQuestions, collapsed, planning }: Props) {
  const [open, setOpen] = useState(true);

  // 回答开始流式输出（collapsed=true）后自动收起；只收不展，用户手动展开后不再打扰
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
