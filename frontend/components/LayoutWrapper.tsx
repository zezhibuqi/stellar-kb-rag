"use client";

import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Dropdown, Layout, Menu, Typography } from "antd";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { clearAuth, getStoredUser, type UserInfo } from "@/lib/api";

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
    { key: "/chat", label: "知识问答" },
    ...(user.role === "aftersale" || user.role === "admin"
      ? [{ key: "/orders", label: "订单数据" }]
      : []),
    ...(user.role === "admin"
      ? [
          { key: "/knowledge", label: "知识库管理" },
          { key: "/users", label: "用户管理" },
          { key: "/settings", label: "模型设置" },
        ]
      : []),
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
      >
        <Typography.Text strong style={{ color: "#fff", fontSize: 16 }}>
          星辰科技集团 · 知识问答
        </Typography.Text>
        <Dropdown
          menu={{
            items: [
              { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: logout },
            ],
          }}
        >
          <span style={{ color: "#fff", cursor: "pointer" }}>
            <Avatar size="small" icon={<UserOutlined />} style={{ marginRight: 8 }} />
            {user.display_name || user.username}（{user.role}）
          </span>
        </Dropdown>
      </Layout.Header>
      <Layout>
        <Layout.Sider width={200} theme="light">
          <Menu
            mode="inline"
            selectedKeys={[pathname]}
            items={menuItems}
            onClick={({ key }) => router.push(key)}
            style={{ height: "100%", borderRight: 0 }}
          />
        </Layout.Sider>
        <Layout.Content style={{ padding: 24 }}>{children}</Layout.Content>
      </Layout>
    </Layout>
  );
}
