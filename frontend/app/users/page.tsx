"use client";

import { PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import LayoutWrapper from "@/components/LayoutWrapper";
import {
  ApiRequestError,
  activateUser,
  createUser,
  deleteUser,
  getStoredUser,
  listUsers,
  resetUserPassword,
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
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [resetTarget, setResetTarget] = useState<UserInfo | null>(null);
  const [resetting, setResetting] = useState(false);
  const [createForm] = Form.useForm();
  const [resetForm] = Form.useForm();
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
      values = await createForm.validateFields();
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
      setCreateModalOpen(false);
      createForm.resetFields();
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

  const handleResetPassword = async () => {
    if (!resetTarget) return;
    let values: { new_password: string };
    try {
      values = await resetForm.validateFields();
    } catch {
      return;
    }
    setResetting(true);
    try {
      await resetUserPassword(resetTarget.id, values.new_password);
      message.success("密码已重置，该账号需重新登录");
      setResetTarget(null);
      resetForm.resetFields();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "重置密码失败"
      );
    } finally {
      setResetting(false);
    }
  };

  const handleDelete = async (user: UserInfo) => {
    try {
      await deleteUser(user.id);
      message.success(`账号 ${user.username} 已停用`);
      loadUsers();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "停用账号失败"
      );
    }
  };

  const handleActivate = async (user: UserInfo) => {
    try {
      await activateUser(user.id);
      message.success(`账号 ${user.username} 已恢复启用`);
      loadUsers();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "启用账号失败"
      );
    }
  };

  const sortedUsers = [...users].sort(
    (a, b) => Number(b.is_active ?? 1) - Number(a.is_active ?? 1)
  );

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
      title: "状态",
      dataIndex: "is_active",
      width: 100,
      render: (isActive: boolean | undefined) =>
        isActive === false ? (
          <Tag color="red">已停用</Tag>
        ) : (
          <Tag color="green">启用</Tag>
        ),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      render: (value: string) => value || "-",
    },
    {
      title: "操作",
      width: 240,
      render: (_: unknown, record: UserInfo) => (
        <Space size="small">
          <Button
            size="small"
            onClick={() => {
              setResetTarget(record);
              resetForm.resetFields();
            }}
          >
            重置密码
          </Button>
          {record.is_active === false ? (
            <Button size="small" type="primary" ghost onClick={() => handleActivate(record)}>
              启用
            </Button>
          ) : (
            <Popconfirm
              title="确认停用该账号？"
              description="停用后无法登录，可随时恢复启用"
              onConfirm={() => handleDelete(record)}
              okText="停用"
              cancelText="取消"
            >
              <Button size="small" danger disabled={record.id === current?.id}>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
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
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateModalOpen(true)}
        >
          新建用户
        </Button>
      </Space>
      <Table
        rowKey="id"
        dataSource={sortedUsers}
        columns={columns}
        loading={loading}
        pagination={false}
      />
      <Modal
        title="新建用户"
        open={createModalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => {
          setCreateModalOpen(false);
          createForm.resetFields();
        }}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical">
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
      <Modal
        title={`重置密码：${resetTarget?.username ?? ""}`}
        open={!!resetTarget}
        onOk={handleResetPassword}
        confirmLoading={resetting}
        onCancel={() => {
          setResetTarget(null);
          resetForm.resetFields();
        }}
        okText="重置"
        cancelText="取消"
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[{ required: true, min: 6, message: "密码至少 6 位" }]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("new_password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </LayoutWrapper>
  );
}
