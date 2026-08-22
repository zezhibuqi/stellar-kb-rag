const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:5000";

const TOKEN_KEY = "token";
const USER_KEY = "user";

export interface ApiError {
  error: string;
  code?: string;
}

export class ApiRequestError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

export interface UserInfo {
  id: number;
  username: string;
  role: string;
  is_active?: boolean;
  display_name?: string | null;
  created_at?: string;
}

export interface LoginResponse {
  token: string;
  user: UserInfo;
}

export interface CreateUserPayload {
  username: string;
  password: string;
  display_name?: string;
  role: string;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

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

export function setStoredUser(user: UserInfo): void {
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearStoredUser(): void {
  window.localStorage.removeItem(USER_KEY);
}

export function clearAuth(): void {
  clearToken();
  clearStoredUser();
}

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

export function login(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function getMe(): Promise<UserInfo> {
  return request<UserInfo>("/api/auth/me");
}

export function listUsers(): Promise<UserInfo[]> {
  return request<UserInfo[]>("/api/users");
}

export function createUser(payload: CreateUserPayload): Promise<UserInfo> {
  return request<UserInfo>("/api/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateUserRole(id: number, role: string): Promise<{ id: number; role: string }> {
  return request<{ id: number; role: string }>(`/api/users/${id}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export function deleteUser(id: number): Promise<{ id: number; is_active: boolean }> {
  return request<{ id: number; is_active: boolean }>(`/api/users/${id}`, {
    method: "DELETE",
  });
}

export function activateUser(id: number): Promise<{ id: number; is_active: boolean }> {
  return request<{ id: number; is_active: boolean }>(`/api/users/${id}/active`, {
    method: "PUT",
    body: JSON.stringify({ is_active: true }),
  });
}

export function resetUserPassword(
  id: number,
  newPassword: string
): Promise<{ id: number; message: string }> {
  return request<{ id: number; message: string }>(`/api/users/${id}/password`, {
    method: "PUT",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export interface DocInfo {
  id: number;
  filename: string;
  domain: string;
  chunk_count: number;
  status: string;
  uploaded_at: string;
}

export interface DocStatus {
  doc_id: number;
  status: string;
  chunk_count: number;
  error?: string;
}

export interface UploadResponse {
  doc_id: number;
  status: string;
}

export function listDocs(domain?: string): Promise<DocInfo[]> {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  return request<DocInfo[]>(`/api/docs${query}`);
}

export function uploadDoc(file: File, domain: string): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file);
  body.append("domain", domain);
  return request<UploadResponse>("/api/upload", { method: "POST", body });
}

export function getDocStatus(docId: number): Promise<DocStatus> {
  return request<DocStatus>(`/api/docs/${docId}/status`);
}

export function deleteDoc(docId: number): Promise<{ message: string }> {
  return request<{ message: string }>(`/api/docs/${docId}`, { method: "DELETE" });
}

export function getDocRaw(docId: number): Promise<RawDoc> {
  return request<RawDoc>(`/api/docs/${docId}/raw`);
}

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

export interface OrderListResponse {
  items: OrderInfo[];
  total: number;
  page: number;
  page_size: number;
}

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

export interface ModelProviderInfo {
  id: string;
  name: string;
  platform: string;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  active: boolean;
}

export interface ModelSettings {
  active: string;
  default: string;
  providers: ModelProviderInfo[];
}

export function getModelSettings(): Promise<ModelSettings> {
  return request<ModelSettings>("/api/settings/model");
}

export function switchModel(providerId: string): Promise<ModelSettings> {
  return request<ModelSettings>("/api/settings/model", {
    method: "PUT",
    body: JSON.stringify({ provider_id: providerId }),
  });
}

export function testModel(
  providerId: string
): Promise<{ ok: boolean; provider_id: string; model: string; reply: string }> {
  return request<{ ok: boolean; provider_id: string; model: string; reply: string }>(
    "/api/settings/model/test",
    { method: "POST", body: JSON.stringify({ provider_id: providerId }) }
  );
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatSource {
  filename: string;
  domain: string;
  content_preview: string;
  source_type?: "vector" | "database";
  doc_id: number | null;
  chunk_id: number | null;
  chunk_type: "text" | "table" | null;
  start_line: number | null;
}

export interface RawDoc {
  filename: string;
  domain: string;
  content: string;
}

export interface ChatResponse {
  answer: string;
  sources: ChatSource[];
}

export function chat(question: string, history: ChatMessage[]): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ question, history, stream: false }),
  });
}

export async function chatStream(
  question: string,
  history: ChatMessage[],
  onToken: (token: string) => void,
  onDone: (sources: ChatSource[]) => void,
  signal?: AbortSignal,
  onError?: (error: string) => void
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, history, stream: true }),
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
        if (typeof event.token === "string") {
          onToken(event.token);
        }
        if (typeof event.error === "string") {
          onError?.(event.error);
        }
        if (event.done) {
          onDone((event.sources as ChatSource[]) ?? []);
        }
      } catch {
        // 忽略无法解析的事件
      }
    }
  }
}
