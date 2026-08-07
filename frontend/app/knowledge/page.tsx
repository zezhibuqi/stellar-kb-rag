"use client";

import { DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
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

const DOMAIN_OPTIONS = [
  { value: "finance", label: "财务数据" },
  { value: "regulation", label: "规章制度" },
  { value: "product", label: "产品规格" },
  { value: "aftersale", label: "售后政策" },
  { value: "common", label: "公共知识" },
];

const STATUS_COLORS: Record<string, string> = {
  pending: "orange",
  processing: "blue",
  completed: "green",
  failed: "red",
};

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
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status] ?? "default"}>{status}</Tag>
      ),
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
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        知识库管理
      </Typography.Title>
      <Card title="上传 Markdown 文档" style={{ marginBottom: 16 }}>
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
          {polling && <Tag color="blue">正在轮询灌库状态（每 2 秒）</Tag>}
        </Space>
      </Card>
      <Table
        rowKey="id"
        dataSource={docs}
        columns={columns}
        loading={loading}
        pagination={false}
      />
    </LayoutWrapper>
  );
}
