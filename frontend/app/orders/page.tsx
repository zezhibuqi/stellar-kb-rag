"use client";

import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { Dayjs } from "dayjs";
import { useCallback, useEffect, useState } from "react";
import LayoutWrapper from "@/components/LayoutWrapper";
import {
  ApiRequestError,
  listOrders,
  type OrderInfo,
} from "@/lib/api";

const PRODUCT_OPTIONS = [
  { value: "SC-100", label: "SC-100" },
  { value: "SC-200", label: "SC-200" },
  { value: "SC-300", label: "SC-300" },
  { value: "SC-400", label: "SC-400" },
  { value: "SC-500", label: "SC-500" },
];

const PAYMENT_OPTIONS = [
  { value: "支付宝", label: "支付宝" },
  { value: "微信支付", label: "微信支付" },
  { value: "银行转账", label: "银行转账" },
  { value: "对公转账", label: "对公转账" },
];

const STATUS_OPTIONS = [
  { value: "completed", label: "已完成" },
  { value: "pending", label: "未完成" },
];

interface Filters {
  order_no?: string;
  customer_name?: string;
  product_type?: string;
  payment_method?: string;
  status?: string;
  created_from?: string;
  created_to?: string;
}

export default function OrdersPage() {
  const [data, setData] = useState<{ items: OrderInfo[]; total: number }>({
    items: [],
    total: 0,
  });
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filters, setFilters] = useState<Filters>({});
  const [form] = Form.useForm();

  const load = useCallback(async (currentPage: number, size: number, query: Filters) => {
    setLoading(true);
    try {
      const result = await listOrders({ ...query, page: currentPage, page_size: size });
      setData({ items: result.items, total: result.total });
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载订单数据失败"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(page, pageSize, filters);
  }, [load, page, pageSize, filters]);

  const handleSearch = () => {
    const values = form.getFieldsValue();
    const range = values.range as [Dayjs, Dayjs] | undefined;
    setFilters({
      order_no: values.order_no?.trim() || undefined,
      customer_name: values.customer_name?.trim() || undefined,
      product_type: values.product_type || undefined,
      payment_method: values.payment_method || undefined,
      status: values.status || undefined,
      created_from: range?.[0] ? range[0].format("YYYY-MM-DD") : undefined,
      created_to: range?.[1] ? range[1].format("YYYY-MM-DD") : undefined,
    });
    setPage(1);
  };

  const handleReset = () => {
    form.resetFields();
    setFilters({});
    setPage(1);
  };

  const columns = [
    { title: "订单号", dataIndex: "order_no", width: 150 },
    { title: "客户", dataIndex: "customer_name" },
    { title: "联系方式", dataIndex: "contact", width: 120 },
    { title: "产品型号", dataIndex: "product_type", width: 100 },
    { title: "数量", dataIndex: "quantity", width: 70 },
    { title: "创建时间", dataIndex: "created_at", width: 170 },
    {
      title: "完成时间",
      dataIndex: "completed_at",
      width: 170,
      render: (value: string | null) => value || "未完成",
    },
    { title: "支付方式", dataIndex: "payment_method", width: 100 },
    {
      title: "总金额(元)",
      dataIndex: "total_amount",
      width: 110,
      render: (value: number) => value.toFixed(2),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (value: "completed" | "pending") =>
        value === "completed" ? (
          <Tag color="green">已完成</Tag>
        ) : (
          <Tag color="red">未完成</Tag>
        ),
    },
  ];

  return (
    <LayoutWrapper>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        订单数据
      </Typography.Title>
      <Card style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline">
          <Space wrap size={8}>
            <Form.Item name="order_no" label="订单号">
              <Input placeholder="如 DD20260315004" allowClear style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="customer_name" label="客户">
              <Input placeholder="客户姓名" allowClear style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="product_type" label="产品">
              <Select options={PRODUCT_OPTIONS} allowClear placeholder="全部" style={{ width: 110 }} />
            </Form.Item>
            <Form.Item name="payment_method" label="支付方式">
              <Select options={PAYMENT_OPTIONS} allowClear placeholder="全部" style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="status" label="状态">
              <Select options={STATUS_OPTIONS} allowClear placeholder="全部" style={{ width: 110 }} />
            </Form.Item>
            <Form.Item name="range" label="创建时间">
              <DatePicker.RangePicker />
            </Form.Item>
            <Form.Item>
              <Space>
                <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
                  查询
                </Button>
                <Button icon={<ReloadOutlined />} onClick={handleReset}>
                  重置
                </Button>
              </Space>
            </Form.Item>
          </Space>
        </Form>
      </Card>
      <Table
        rowKey="order_no"
        dataSource={data.items}
        columns={columns}
        loading={loading}
        pagination={{
          current: page,
          pageSize,
          total: data.total,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 条`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
          },
        }}
      />
    </LayoutWrapper>
  );
}
