"use client";

import {
  ControlOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MessageOutlined,
  StarFilled,
  TeamOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Avatar, Dropdown, Layout, Menu, Typography } from "antd";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { clearAuth, getStoredUser, type UserInfo } from "@/lib/api";

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

export default function LayoutWrapper({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<UserInfo | null>(null);
  const [ready, setReady] = useState(false);

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
          borderBottom: "1px solid #f0f1f4",
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
        <Dropdown
          menu={{
            items: [
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
            <Avatar size={26} icon={<UserOutlined />} style={{ background: "#e8f3ff", color: "#1677ff" }} />
            <Typography.Text style={{ fontSize: 13 }}>
              {user.display_name || user.username}
            </Typography.Text>
          </span>
        </Dropdown>
      </Layout.Header>
      <Layout>
        <Layout.Sider
          width={216}
          theme="light"
          style={{ borderRight: "1px solid #f0f1f4" }}
        >
          <Menu
            mode="inline"
            selectedKeys={[pathname]}
            items={menuItems}
            onClick={({ key }) => router.push(key)}
            style={{ height: "100%", borderRight: 0, paddingTop: 8 }}
          />
        </Layout.Sider>
        <Layout.Content style={{ padding: "24px 28px" }}>{children}</Layout.Content>
      </Layout>
    </Layout>
  );
}
