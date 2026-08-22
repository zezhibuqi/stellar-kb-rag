"use client";

import { CheckCircleOutlined, ApiOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Col,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";
import LayoutWrapper from "@/components/LayoutWrapper";
import {
  ApiRequestError,
  getModelSettings,
  switchModel,
  testModel,
  type ModelSettings,
} from "@/lib/api";

interface TestState {
  status: "idle" | "testing" | "ok" | "failed";
  detail?: string;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSettings(await getModelSettings());
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "加载模型设置失败"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSwitch = async (providerId: string) => {
    setSwitching(providerId);
    try {
      setSettings(await switchModel(providerId));
      message.success("当前模型已切换");
    } catch (error) {
      message.error(
        error instanceof ApiRequestError ? error.message : "切换失败，请稍后重试"
      );
    } finally {
      setSwitching(null);
    }
  };

  const handleTest = async (providerId: string, providerName: string) => {
    setTests((prev) => ({ ...prev, [providerId]: { status: "testing" } }));
    try {
      const result = await testModel(providerId);
      setTests((prev) => ({
        ...prev,
        [providerId]: { status: "ok", detail: `连接正常（${result.model}）` },
      }));
    } catch (error) {
      const detail =
        error instanceof ApiRequestError ? error.message : "连接失败，请稍后重试";
      setTests((prev) => ({ ...prev, [providerId]: { status: "failed", detail } }));
      message.warning(`${providerName}：${detail}`);
    }
  };

  return (
    <LayoutWrapper>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        模型设置
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        切换问答使用的当前模型（回答生成与意图路由同时生效）；切换立即对后续提问生效，不影响进行中的回答。
      </Typography.Paragraph>

      {loading && !settings ? (
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin />
        </div>
      ) : (
        <Row gutter={[16, 16]}>
          {settings?.providers.map((provider) => {
            const test = tests[provider.id] ?? { status: "idle" as const };
            return (
              <Col key={provider.id} xs={24} md={12}>
                <Card
                  title={
                    <Space>
                      <span>{provider.name}</span>
                      {provider.active && <Tag color="green">使用中</Tag>}
                    </Space>
                  }
                  extra={
                    provider.active ? (
                      <CheckCircleOutlined style={{ color: "#52c41a", fontSize: 18 }} />
                    ) : null
                  }
                >
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Typography.Text type="secondary">{provider.platform}</Typography.Text>
                    <div>
                      模型标识：<Typography.Text code>{provider.model}</Typography.Text>
                    </div>
                    <div>
                      接口地址：<Typography.Text code>{provider.base_url}</Typography.Text>
                    </div>
                    <div>
                      API Key：
                      {provider.api_key_configured ? (
                        <Tag color="green">已配置</Tag>
                      ) : (
                        <Tag color="red">未配置</Tag>
                      )}
                    </div>
                    {test.status === "ok" && (
                      <Typography.Text type="success">{test.detail}</Typography.Text>
                    )}
                    {test.status === "failed" && (
                      <Typography.Text type="danger">{test.detail}</Typography.Text>
                    )}
                    <Space style={{ marginTop: 8 }}>
                      <Button
                        type="primary"
                        disabled={provider.active || !provider.api_key_configured}
                        loading={switching === provider.id}
                        onClick={() => handleSwitch(provider.id)}
                      >
                        切换为当前模型
                      </Button>
                      <Button
                        icon={<ApiOutlined />}
                        disabled={!provider.api_key_configured}
                        loading={test.status === "testing"}
                        onClick={() => handleTest(provider.id, provider.name)}
                      >
                        测试连接
                      </Button>
                    </Space>
                    {!provider.api_key_configured && (
                      <Typography.Text type="warning" style={{ fontSize: 12 }}>
                        请在服务端 .env 中配置对应 API Key 后重启后端，方可切换。
                      </Typography.Text>
                    )}
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      <Space style={{ marginTop: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新
        </Button>
      </Space>
    </LayoutWrapper>
  );
}
