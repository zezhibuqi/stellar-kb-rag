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
