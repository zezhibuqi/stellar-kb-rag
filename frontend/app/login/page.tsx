"use client";

/**
 * 登录页：账号密码表单 + 主题切换；登录成功后把 token 与用户信息写入 localStorage。
 *
 * 系统不提供注册入口（用户由管理员创建），登录页也不做任何权限判断——
 * 角色相关的菜单与路由守卫在 LayoutWrapper 内完成。
 */
import { MoonOutlined, StarFilled, SunOutlined } from "@ant-design/icons";
import { Button, Form, Input, Typography, message } from "antd";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ApiRequestError,
  getStoredUser,
  login,
  setStoredUser,
  setToken,
} from "@/lib/api";
import { useThemeMode } from "@/components/ThemeProvider";

/** 登录页组件。 */
export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const { mode, toggle } = useThemeMode();

  useEffect(() => {
    // 已登录（本地有用户信息）直接进问答页，避免重复登录
    if (getStoredUser()) {
      router.replace("/chat");
    }
  }, [router]);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const result = await login(values.username, values.password);
      setToken(result.token);
      setStoredUser(result.user);
      message.success("登录成功");
      router.replace("/chat");
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "登录失败，请稍后重试"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--app-bg)",
        position: "relative",
      }}
    >
      <Button
        type="text"
        shape="circle"
        icon={mode === "dark" ? <SunOutlined /> : <MoonOutlined />}
        onClick={toggle}
        title={mode === "dark" ? "切换到亮色模式" : "切换到夜间模式"}
        style={{ position: "absolute", top: 20, right: 20 }}
      />
      <div style={{ width: 392 }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 44,
              height: 44,
              borderRadius: 12,
              background: "linear-gradient(135deg, #1677ff 0%, #4096ff 100%)",
              color: "#fff",
              fontSize: 20,
              boxShadow: "0 4px 12px rgba(22, 119, 255, 0.28)",
            }}
          >
            <StarFilled />
          </div>
          <Typography.Title level={3} style={{ marginTop: 16, marginBottom: 4 }}>
            星辰知识库
          </Typography.Title>
          <Typography.Text type="secondary">
            企业知识问答系统 · 按角色权限检索五大知识领域
          </Typography.Text>
        </div>
        <div className="app-card" style={{ padding: "28px 28px 24px" }}>
          <Form onFinish={onFinish} layout="vertical" size="large">
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: true, message: "请输入用户名" }]}
            >
              <Input autoComplete="username" placeholder="请输入用户名" />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: "请输入密码" }]}
            >
              <Input.Password autoComplete="current-password" placeholder="请输入密码" />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={loading}
              style={{ marginTop: 4, height: 42 }}
            >
              登录
            </Button>
          </Form>
        </div>
        <Typography.Paragraph
          type="secondary"
          style={{ textAlign: "center", fontSize: 12, marginTop: 24 }}
        >
          数据来源于公开渠道并已脱敏 · 仅用于学术研究
        </Typography.Paragraph>
      </div>
    </div>
  );
}
