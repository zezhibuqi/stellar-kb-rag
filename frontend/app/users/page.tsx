"use client";

import { PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import LayoutWrapper from "@/components/LayoutWrapper";
import {
  ApiRequestError,
  createUser,
  getStoredUser,
  listUsers,
  updateUserRole,
  type UserInfo,
} from "@/lib/api";

const ROLE_OPTIONS = [
  { value: "employee", label: "普通员工" },
  { value: "finance", label: "财务人员" },
  { value: "sales", label: "销售人员" },
  { value: "aftersale", label: "售后人员" },
  { value: "admin", label: "系统管理员" },
];

export default function UsersPage() {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();
  const current = getStoredUser();

  const loadUsers = async () => {
    setLoading(true);
    try {
      setUsers(await listUsers());
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载用户列表失败"
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const handleCreate = async () => {
    let values: {
      username: string;
      password: string;
      display_name?: string;
      role?: string;
    };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      await createUser({
        username: values.username,
        password: values.password,
        display_name: values.display_name,
        role: values.role ?? "employee",
      });
      message.success("用户创建成功");
      setModalOpen(false);
      form.resetFields();
      loadUsers();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "创建用户失败"
      );
    } finally {
      setCreating(false);
    }
  };

  const handleRoleChange = async (userId: number, role: string) => {
    try {
      await updateUserRole(userId, role);
      message.success("角色已更新");
      loadUsers();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "修改角色失败"
      );
      loadUsers();
    }
  };

  const columns = [
    { title: "ID", dataIndex: "id", width: 60 },
    { title: "用户名", dataIndex: "username" },
    {
      title: "显示名",
      dataIndex: "display_name",
      render: (value: string | null | undefined) => value || "-",
    },
    {
      title: "角色",
      dataIndex: "role",
      render: (role: string, record: UserInfo) => (
        <Select
          value={role}
          options={ROLE_OPTIONS}
          disabled={record.id === current?.id}
          onChange={(value) => handleRoleChange(record.id, value)}
          style={{ width: 130 }}
        />
      ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      render: (value: string) => value || "-",
    },
  ];

  return (
    <LayoutWrapper>
      <Space
        style={{ marginBottom: 16, justifyContent: "space-between", width: "100%" }}
        align="center"
      >
        <Typography.Title level={3} style={{ margin: 0 }}>
          用户管理
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          新建用户
        </Button>
      </Space>
      <Table
        rowKey="id"
        dataSource={users}
        columns={columns}
        loading={loading}
        pagination={false}
      />
      <Modal
        title="新建用户"
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, min: 6, message: "密码至少 6 位" }]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="employee">
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </LayoutWrapper>
  );
}
