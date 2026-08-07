const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:5000";

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

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("token");
}

export function setToken(token: string): void {
  window.localStorage.setItem("token", token);
}

export function clearToken(): void {
  window.localStorage.removeItem("token");
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
