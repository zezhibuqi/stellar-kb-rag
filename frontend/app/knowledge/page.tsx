"use client";

/**
 * 知识库管理页（仅 admin）：上传 Markdown → 后台异步灌库 → 轮询状态 → 列表增删。
 *
 * 灌库是异步的：上传接口立即返回 pending 与 doc_id，本页按 2 秒间隔轮询状态，
 * 终态（completed/failed）时停止轮询并刷新列表。
 */
import { DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import {
  Badge,
  Button,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
  Upload,
  message,
} from "antd";
import { useEffect, useRef, useState } from "react";
import LayoutWrapper from "@/components/LayoutWrapper";
import {
  ApiRequestError,
  deleteDoc,
  getDocStatus,
  listDocs,
  uploadDoc,
  type DocInfo,
} from "@/lib/api";

/** 上传时可选的五个知识领域（value 与后端 domains.name 一致）。 */
const DOMAIN_OPTIONS = [
  { value: "finance", label: "财务数据" },
  { value: "regulation", label: "规章制度" },
  { value: "product", label: "产品规格" },
  { value: "aftersale", label: "售后政策" },
  { value: "common", label: "公共知识" },
];

/** 文档状态 → 徽标样式与中文文案（对应后端的四种状态）。 */
const STATUS_BADGES: Record<string, { status: "default" | "processing" | "success" | "error"; label: string }> = {
  pending: { status: "default", label: "待处理" },
  processing: { status: "processing", label: "灌库中" },
  completed: { status: "success", label: "已完成" },
  failed: { status: "error", label: "失败" },
};

/** 知识库管理页组件：自持文档列表、上传表单与轮询定时器。 */
export default function KnowledgePage() {
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [domain, setDomain] = useState<string | undefined>();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [polling, setPolling] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadDocs = async () => {
    setLoading(true);
    try {
      setDocs(await listDocs(domain));
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载文档列表失败"
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDocs();
  }, [domain]);

  useEffect(
    () => () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    },
    []
  );

  const startPolling = (docId: number) => {
    // 轮询间隔固定 2 秒：灌库耗时取决于 Embedding 接口，轮询过密没有收益
    setPolling(true);
    pollTimer.current = setInterval(async () => {
      try {
        const status = await getDocStatus(docId);
        if (status.status === "completed" || status.status === "failed") {
          if (pollTimer.current) clearInterval(pollTimer.current);
          setPolling(false);
          if (status.status === "completed") {
            message.success("文档灌库完成");
          } else {
            message.error(`灌库失败：${status.error ?? "未知错误"}`);
          }
          loadDocs();
        }
      } catch (error) {
        if (pollTimer.current) clearInterval(pollTimer.current);
        setPolling(false);
        message.error(
          error instanceof ApiRequestError ? error.message : "查询灌库状态失败"
        );
      }
    }, 2000);
  };

  const handleUpload = async () => {
    if (!file || !domain) {
      message.warning("请先选择领域和文件");
      return;
    }
    setUploading(true);
    try {
      const result = await uploadDoc(file, domain);
      message.success("上传成功，开始异步灌库");
      setFile(null);
      startPolling(result.doc_id);
      loadDocs();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "上传失败"
      );
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (docId: number) => {
    try {
      await deleteDoc(docId);
      message.success("文档已删除");
      loadDocs();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "删除失败"
      );
    }
  };

  const columns = [
    { title: "ID", dataIndex: "id", width: 60 },
    { title: "文件名", dataIndex: "filename" },
    { title: "领域", dataIndex: "domain", width: 110 },
    { title: "切块数", dataIndex: "chunk_count", width: 90 },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (status: string) => {
        const badge = STATUS_BADGES[status] ?? { status: "default" as const, label: status };
        return <Badge status={badge.status} text={badge.label} />;
      },
    },
    {
      title: "上传时间",
      dataIndex: "uploaded_at",
      render: (value: string) => value || "-",
    },
    {
      title: "操作",
      width: 90,
      render: (_: unknown, record: DocInfo) => (
        <Popconfirm
          title="确认删除该文档？"
          description="删除后向量与记录将同步清除"
          onConfirm={() => handleDelete(record.id)}
          okText="删除"
          cancelText="取消"
        >
          <Button danger size="small" icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <LayoutWrapper>
      <div className="page-header">
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            知识库管理
          </Typography.Title>
          <Typography.Text type="secondary">
            上传 Markdown 文档，异步执行切块与向量化灌库
          </Typography.Text>
        </div>
      </div>
      <div className="app-card" style={{ padding: "16px 20px", marginBottom: 16 }}>
        <Space wrap align="center">
          <Select
            placeholder="选择领域"
            options={DOMAIN_OPTIONS}
            value={domain}
            onChange={setDomain}
            style={{ width: 160 }}
          />
          <Upload
            accept=".md"
            maxCount={1}
            beforeUpload={(f) => {
              setFile(f);
              return false;
            }}
            onRemove={() => setFile(null)}
            fileList={
              file ? [{ uid: "-1", name: file.name, status: "done" }] : []
            }
          >
            <Button icon={<UploadOutlined />}>选择文件</Button>
          </Upload>
          <Button
            type="primary"
            onClick={handleUpload}
            loading={uploading}
            disabled={polling}
          >
            上传并灌库
          </Button>
          {polling && <Badge status="processing" text="正在轮询灌库状态（每 2 秒）" />}
        </Space>
      </div>
      <div className="app-card" style={{ overflow: "hidden" }}>
        <Table
          rowKey="id"
          dataSource={docs}
          columns={columns}
          loading={loading}
          pagination={false}
        />
      </div>
    </LayoutWrapper>
  );
}
