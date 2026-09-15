"use client";

/**
 * 全局布局：顶栏（品牌 + 主题切换 + 用户菜单）、左侧导航与内容区。
 *
 * 同时承担两个前端侧的粗粒度守卫：
 * - 未登录（localStorage 无用户）直接跳 /login；
 * - 非管理员访问 /knowledge、/users、/settings，或非 aftersale/admin 访问 /orders 时跳回 /chat。
 * 真正的权限判断仍在服务端——这里只是避免用户看到必然 403 的页面。
 */
import {
  ControlOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  KeyOutlined,
  LogoutOutlined,
  MessageOutlined,
  MoonOutlined,
  StarFilled,
  SunOutlined,
  TeamOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  Avatar,
  Button,
  Dropdown,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Typography,
  message,
} from "antd";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  ApiRequestError,
  changeMyPassword,
  clearAuth,
  getStoredUser,
  setToken,
  type UserInfo,
} from "@/lib/api";
import { useThemeMode } from "@/components/ThemeProvider";

/** 顶栏左侧的品牌标识（内联样式的渐变小方块 + 星标）。 */
const BRAND_MARK = (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      width: 28,
      height: 28,
      borderRadius: 8,
      background: "linear-gradient(135deg, #1677ff 0%, #4096ff 100%)",
      color: "#fff",
      fontSize: 13,
    }}
  >
    <StarFilled />
  </div>
);

/**
 * 布局组件：包装所有登录后页面。
 *
 * 修改密码成功后会把服务端返回的新 token 写回本地——旧 token 因 token_version 自增
 * 已失效，不替换会导致下一次请求 401 被踢回登录页。
 */
export default function LayoutWrapper({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<UserInfo | null>(null);
  const [ready, setReady] = useState(false);
  const { mode, toggle } = useThemeMode();
  const [pwdOpen, setPwdOpen] = useState(false);
  const [pwdLoading, setPwdLoading] = useState(false);
  const [pwdForm] = Form.useForm();

  useEffect(() => {
    const stored = getStoredUser();
    if (!stored) {
      router.replace("/login");
      return;
    }
    setUser(stored);
    setReady(true);
  }, [router]);

  useEffect(() => {
    if (!user) return;
    // 页面级守卫：与菜单项的可⻅性保持一致，防止用户手敲 URL 进入无权限页面
    const adminOnly =
      pathname === "/knowledge" || pathname === "/users" || pathname === "/settings";
    const ordersPage = pathname === "/orders";
    const canViewOrders = user.role === "aftersale" || user.role === "admin";
    if (adminOnly && user.role !== "admin") {
      router.replace("/chat");
    }
    if (ordersPage && !canViewOrders) {
      router.replace("/chat");
    }
  }, [pathname, router, user]);

  if (!ready || !user) return null;

  const logout = () => {
    clearAuth();
    router.replace("/login");
  };

  const handleChangePassword = async () => {
    let values: { old_password: string; new_password: string };
    try {
      values = await pwdForm.validateFields();
    } catch {
      return;
    }
    setPwdLoading(true);
    try {
      const result = await changeMyPassword(
        values.old_password,
        values.new_password
      );
      setToken(result.token);
      message.success("密码已修改");
      setPwdOpen(false);
      pwdForm.resetFields();
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "修改密码失败，请稍后重试"
      );
    } finally {
      setPwdLoading(false);
    }
  };

  const menuItems = [
    { key: "/chat", icon: <MessageOutlined />, label: "知识问答" },
    ...(user.role === "aftersale" || user.role === "admin"
      ? [{ key: "/orders", icon: <FileTextOutlined />, label: "订单数据" }]
      : []),
    ...(user.role === "admin"
      ? [
          { key: "/knowledge", icon: <DatabaseOutlined />, label: "知识库管理" },
          { key: "/users", icon: <TeamOutlined />, label: "用户管理" },
          { key: "/settings", icon: <ControlOutlined />, label: "模型设置" },
        ]
      : []),
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingInline: 24,
          borderBottom: "1px solid var(--app-card-border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {BRAND_MARK}
          <Typography.Text strong style={{ fontSize: 15, letterSpacing: 0.2 }}>
            星辰知识库
          </Typography.Text>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, marginLeft: 2 }}
          >
            企业知识问答
          </Typography.Text>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Button
            type="text"
            shape="circle"
            icon={mode === "dark" ? <SunOutlined /> : <MoonOutlined />}
            onClick={toggle}
            title={mode === "dark" ? "切换到亮色模式" : "切换到夜间模式"}
          />
          <Dropdown
            menu={{
              items: [
                {
                  key: "password",
                  icon: <KeyOutlined />,
                  label: "修改密码",
                  onClick: () => setPwdOpen(true),
                },
                { type: "divider" },
                { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: logout },
              ],
            }}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                cursor: "pointer",
                padding: "4px 8px",
                borderRadius: 8,
                transition: "background 0.15s",
              }}
              className="user-dropdown-trigger"
            >
              <Avatar
                size={26}
                icon={<UserOutlined />}
                style={
                  mode === "dark"
                    ? { background: "rgba(55, 148, 255, 0.16)", color: "#3794ff" }
                    : { background: "#e8f3ff", color: "#1677ff" }
                }
              />
              <Typography.Text style={{ fontSize: 13 }}>
                {user.display_name || user.username}
              </Typography.Text>
            </span>
          </Dropdown>
        </div>
      </Layout.Header>
      <Layout>
        <Layout.Sider
          width={216}
          theme="light"
          style={{ borderRight: "1px solid var(--app-card-border)" }}
        >
          <Menu
            mode="inline"
            selectedKeys={[pathname]}
            items={menuItems}
            onClick={({ key }) => router.push(key)}
            style={{ height: "100%", borderRight: 0, paddingTop: 8 }}
          />
        </Layout.Sider>
        <Layout.Content
          style={
            pathname === "/chat"
              ? {
                  height: "calc(100vh - 56px)",
                  display: "flex",
                  flexDirection: "column",
                  overflow: "hidden",
                }
              : { padding: "24px 28px" }
          }
        >
          {children}
        </Layout.Content>
      </Layout>
      <Modal
        title="修改密码"
        open={pwdOpen}
        onOk={handleChangePassword}
        confirmLoading={pwdLoading}
        onCancel={() => {
          setPwdOpen(false);
          pwdForm.resetFields();
        }}
        okText="修改"
        cancelText="取消"
      >
        <Form form={pwdForm} layout="vertical">
          <Form.Item
            name="old_password"
            label="当前密码"
            rules={[{ required: true, message: "请输入当前密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[{ required: true, min: 6, message: "新密码至少 6 位" }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
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
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  );
}
