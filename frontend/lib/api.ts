/**
 * 后端 API 访问层：所有页面的唯一出入口。
 *
 * 约定：
 * - 登录态存在 localStorage（token + 用户信息），请求自动带 Bearer 头；
 * - 后端错误统一为 {error, code}，这里包装成 ApiRequestError（含 HTTP 状态码与业务 code），
 *   页面据此展示提示，必要时按 code 做分支（如 MODE_UNAVAILABLE、CONVERSATION_LIMIT）；
 * - 问答流式接口单独实现（chatStream）：用 fetch + ReadableStream 消费 SSE，
 *   因为 EventSource 只支持 GET，无法发送 POST 与认证头。
 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:5000";

/** localStorage 键：JWT。 */
const TOKEN_KEY = "token";
/** localStorage 键：当前用户信息（仅用于渲染，真正的权限判断在服务端）。 */
const USER_KEY = "user";

/** 后端错误响应体结构。 */
export interface ApiError {
  error: string;
  code?: string;
}

/**
 * 统一的 API 异常：status 为 HTTP 状态码，code 为后端业务错误码。
 *
 * 页面用 `error instanceof ApiRequestError` 判断是否可直接展示 message。
 */
export class ApiRequestError extends Error {
  status: number;
  code?: string;

  /** 构造异常并保留状态码与业务错误码。 */
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

// ── 认证与本地登录态 ─────────────────────────────────────────────

/** 登录接口返回的用户信息（/api/auth/me 的形状）。 */
export interface UserInfo {
  id: number;
  username: string;
  role: string;
  is_active?: boolean;
  display_name?: string | null;
  created_at?: string;
}

/** 登录接口响应：token + 用户信息。 */
export interface LoginResponse {
  token: string;
  user: UserInfo;
}

/** 管理员创建用户的请求体。 */
export interface CreateUserPayload {
  username: string;
  password: string;
  display_name?: string;
  role: string;
}

/** 读取本地 JWT（服务端渲染时为 null）。 */
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

/** 写入本地 JWT（登录成功、改密后换新 token 时调用）。 */
export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

/** 清除本地 JWT。 */
export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

/** 读取本地用户信息（渲染菜单与角色相关入口用）。 */
export function getStoredUser(): UserInfo | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserInfo;
  } catch {
    return null;
  }
}

/** 写入本地用户信息。 */
export function setStoredUser(user: UserInfo): void {
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

/** 清除本地用户信息。 */
export function clearStoredUser(): void {
  window.localStorage.removeItem(USER_KEY);
}

/** 退出登录：同时清掉 token 与用户信息。 */
export function clearAuth(): void {
  clearToken();
  clearStoredUser();
}

/**
 * 通用请求封装：自动补 Content-Type 与 Bearer 头，非 2xx 统一抛 ApiRequestError。
 *
 * FormData 请求不设置 Content-Type（由浏览器补 multipart 边界）。
 */
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    let error: ApiError = { error: `请求失败（HTTP ${response.status}）` };
    try {
      error = (await response.json()) as ApiError;
    } catch {
      // 非 JSON 响应体时保留默认错误信息
    }
    throw new ApiRequestError(response.status, error.error, error.code);
  }
  return (await response.json()) as T;
}

/** 登录：成功返回 token 与用户信息（失败抛 ApiRequestError）。 */
export function login(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

/** 获取当前登录用户（用于校验本地 token 是否仍有效）。 */
export function getMe(): Promise<UserInfo> {
  return request<UserInfo>("/api/auth/me");
}

/**
 * 自助修改密码：验证当前密码后服务端换发新 token（旧 token 立即失效）。
 * 调用方必须用返回值里的新 token 覆盖本地 token，否则下一次请求会 401。
 */
export function changeMyPassword(
  oldPassword: string,
  newPassword: string
): Promise<{ token: string }> {
  return request<{ token: string }>("/api/auth/password", {
    method: "PUT",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}

// ── 用户管理（仅管理员） ──────────────────────────────────────────

/** 用户列表。 */
export function listUsers(): Promise<UserInfo[]> {
  return request<UserInfo[]>("/api/users");
}

/** 创建用户（管理员）。 */
export function createUser(payload: CreateUserPayload): Promise<UserInfo> {
  return request<UserInfo>("/api/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** 修改用户角色（管理员；后端会拦截「最后一个管理员」与「改自己」）。 */
export function updateUserRole(id: number, role: string): Promise<{ id: number; role: string }> {
  return request<{ id: number; role: string }>(`/api/users/${id}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

/** 停用账号（可恢复，用户名仍占用）。 */
export function deactivateUser(
  id: number
): Promise<{ id: number; is_active: boolean }> {
  return request<{ id: number; is_active: boolean }>(`/api/users/${id}/deactivate`, {
    method: "PUT",
  });
}

/** 恢复启用被停用的账号。 */
export function activateUser(id: number): Promise<{ id: number; is_active: boolean }> {
  return request<{ id: number; is_active: boolean }>(`/api/users/${id}/activate`, {
    method: "PUT",
  });
}

/** 永久删除账号（仅限已停用；用户名随之释放）。 */
export function deleteUser(id: number): Promise<{ id: number; deleted: boolean }> {
  return request<{ id: number; deleted: boolean }>(`/api/users/${id}`, {
    method: "DELETE",
  });
}

/** 管理员重置他人密码（目标用户已签发的 token 全部失效）。 */
export function resetUserPassword(
  id: number,
  newPassword: string
): Promise<{ id: number; message: string }> {
  return request<{ id: number; message: string }>(`/api/users/${id}/password`, {
    method: "PUT",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

// ── 知识库管理 ───────────────────────────────────────────────────

/** 文档列表项（不含全文）。 */
export interface DocInfo {
  id: number;
  filename: string;
  domain: string;
  chunk_count: number;
  status: string;
  uploaded_at: string;
}

/** 灌库状态响应（failed 时附带 error）。 */
export interface DocStatus {
  doc_id: number;
  status: string;
  chunk_count: number;
  error?: string;
}

/** 上传接口响应：立即返回的 doc_id 与 pending 状态。 */
export interface UploadResponse {
  doc_id: number;
  status: string;
}

/** 文档列表，可按领域过滤。 */
export function listDocs(domain?: string): Promise<DocInfo[]> {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  return request<DocInfo[]>(`/api/docs${query}`);
}

/** 上传 Markdown（异步灌库，配合 getDocStatus 轮询）。 */
export function uploadDoc(file: File, domain: string): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file);
  body.append("domain", domain);
  return request<UploadResponse>("/api/upload", { method: "POST", body });
}

/** 查询灌库进度。 */
export function getDocStatus(docId: number): Promise<DocStatus> {
  return request<DocStatus>(`/api/docs/${docId}/status`);
}

/** 删除文档（同时清理向量与关键词索引）。 */
export function deleteDoc(docId: number): Promise<{ message: string }> {
  return request<{ message: string }>(`/api/docs/${docId}`, { method: "DELETE" });
}

/** 取原文档全文（用于 /viewer 渲染与定位）。 */
export function getDocRaw(docId: number): Promise<RawDoc> {
  return request<RawDoc>(`/api/docs/${docId}/raw`);
}

// ── 订单数据（仅 aftersale / admin） ──────────────────────────────

/** 订单行（联系方式已由服务端脱敏）。 */
export interface OrderInfo {
  order_no: string;
  customer_name: string;
  contact: string;
  product_type: string;
  quantity: number;
  created_at: string;
  completed_at: string | null;
  payment_method: string;
  total_amount: number;
  status: "completed" | "pending";
}

/** 订单列表查询参数（未给出的键不会被拼进查询串）。 */
export interface OrderListParams {
  order_no?: string;
  customer_name?: string;
  product_type?: string;
  payment_method?: string;
  status?: string;
  created_from?: string;
  created_to?: string;
  page?: number;
  page_size?: number;
}

/** 订单列表分页响应。 */
export interface OrderListResponse {
  items: OrderInfo[];
  total: number;
  page: number;
  page_size: number;
}

/** 订单列表：空值参数自动忽略，避免出现 `?status=` 这类无意义条件。 */
export function listOrders(params: OrderListParams): Promise<OrderListResponse> {
  const query = new URLSearchParams();
  (Object.keys(params) as (keyof OrderListParams)[]).forEach((key) => {
    const value = params[key];
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  });
  return request<OrderListResponse>(`/api/orders?${query.toString()}`);
}

// ── 模型设置（仅管理员） ──────────────────────────────────────────

/** 提供方信息（来自后端注册表；不含密钥值，只有是否已配置）。 */
export interface ModelProviderInfo {
  id: string;
  name: string;
  platform: string;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  agent_capable: boolean;
  active: boolean;
}

/** 模型设置快照：当前模型 + 默认模型 + 全部提供方。 */
export interface ModelSettings {
  active: string;
  default: string;
  providers: ModelProviderInfo[];
}

/** 读取模型设置（也用于问答页判断当前模型是否支持增强模式）。 */
export function getModelSettings(): Promise<ModelSettings> {
  return request<ModelSettings>("/api/settings/model");
}

/** 切换当前模型（服务端校验密钥已配置）。 */
export function switchModel(providerId: string): Promise<ModelSettings> {
  return request<ModelSettings>("/api/settings/model", {
    method: "PUT",
    body: JSON.stringify({ provider_id: providerId }),
  });
}

/** 连通性测试：发一次最小真实调用，失败时返回 502 与原因。 */
export function testModel(
  providerId: string
): Promise<{ ok: boolean; provider_id: string; model: string; reply: string }> {
  return request<{ ok: boolean; provider_id: string; model: string; reply: string }>(
    "/api/settings/model/test",
    { method: "POST", body: JSON.stringify({ provider_id: providerId }) }
  );
}

// ── 会话与消息 ──────────────────────────────────────────────────

/** 发送给模型的历史消息（当前仅用于类型占位，上下文由服务端截取）。 */
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/** 会话列表项。 */
export interface ConversationInfo {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

/** 回放用的完整消息：含模式、状态、来源与增强模式过程记录。 */
export interface StoredMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  mode: string;
  status: "streaming" | "completed" | "failed" | "aborted";
  sources: ChatSource[];
  trace: unknown;
  created_at: string;
}

/** 我的会话列表（按最近更新倒序）。 */
export function listConversations(): Promise<ConversationInfo[]> {
  return request<ConversationInfo[]>("/api/conversations");
}

/** 新建空会话（达到每用户上限时返回 400 CONVERSATION_LIMIT）。 */
export function createConversation(): Promise<{ id: number; title: string }> {
  return request<{ id: number; title: string }>("/api/conversations", {
    method: "POST",
  });
}

/** 回放某个会话的全部消息。 */
export function listConversationMessages(
  conversationId: number
): Promise<StoredMessage[]> {
  return request<StoredMessage[]>(`/api/conversations/${conversationId}/messages`);
}

/** 删除会话（消息级联删除）。 */
export function deleteConversation(
  conversationId: number
): Promise<{ id: number; deleted: boolean }> {
  return request<{ id: number; deleted: boolean }>(
    `/api/conversations/${conversationId}`,
    { method: "DELETE" }
  );
}

/**
 * 引用来源：标准模式为向量来源；增强模式额外带 sub_question_id 用于分组；
 * 订单问答为数据库来源（source_type=database，无 doc_id，不能跳原文）。
 */
export interface ChatSource {
  filename: string;
  domain: string;
  content_preview: string;
  source_type?: "vector" | "database";
  doc_id: number | null;
  chunk_id: number | null;
  chunk_type: "text" | "table" | null;
  start_line: number | null;
  /** 增强模式：该来源支撑的子问题编号；标准模式为 null */
  sub_question_id?: number | null;
}

/** 原文档全文响应。 */
export interface RawDoc {
  filename: string;
  domain: string;
  content: string;
}

/** 非流式问答响应（answer + sources）。 */
export interface ChatResponse {
  answer: string;
  sources: ChatSource[];
}

/** 非流式问答：仅标准模式使用（增强模式必须流式）。 */
export function chat(conversationId: number, question: string): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: conversationId,
      question,
      stream: false,
    }),
  });
}

/**
 * 流式回调集合。
 *
 * 事件分三类：token（增量文本）、stage（增强模式过程）、done/error（终态），
 * 页面通常只实现 onToken / onDone / onStage 三件套。
 */
export interface ChatStreamHandlers {
  onToken: (token: string) => void;
  onDone: (sources: ChatSource[]) => void;
  onMessageId?: (messageId: number) => void;
  onError?: (error: string) => void;
  /** 增强模式的过程事件：planning / planned / sub_answer / synthesizing */
  onStage?: (event: Record<string, unknown>) => void;
}

/**
 * 流式问答：POST /api/chat（stream=true）并用 ReadableStream 逐帧消费 SSE。
 *
 * 与 request() 的区别：需要 POST + 认证头 + 逐块读取，因此单独实现；
 * 传入 AbortSignal 时用户点「停止」会中断连接，服务端把消息置为 aborted。
 */
export async function chatStream(
  conversationId: number,
  question: string,
  mode: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      conversation_id: conversationId,
      question,
      mode,
      stream: true,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    let error: ApiError = { error: `请求失败（HTTP ${response.status}）` };
    try {
      error = (await response.json()) as ApiError;
    } catch {
      // 非 JSON 响应体时保留默认错误信息
    }
    throw new ApiRequestError(response.status, error.error, error.code);
  }

  const reader = response.body.getReader();
  // SSE 以空行分帧：缓冲区里按 \n\n 切分，残帧留到下一块数据再拼
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator: number;
    while ((separator = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      if (!raw.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(raw.slice(6)) as Record<string, unknown>;
        if (typeof event.message_id === "number") {
          handlers.onMessageId?.(event.message_id);
        }
        if (typeof event.stage === "string") {
          handlers.onStage?.(event);
        }
        if (typeof event.token === "string") {
          handlers.onToken(event.token);
        }
        if (typeof event.error === "string") {
          handlers.onError?.(event.error);
        }
        if (event.done) {
          handlers.onDone((event.sources as ChatSource[]) ?? []);
        }
      } catch {
        // 忽略无法解析的事件
      }
    }
  }
}
