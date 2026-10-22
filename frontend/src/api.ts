/** 管理接口错误保留 HTTP 状态，以区分认证失效与未实现功能。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
/** 同源管理客户端；凭据仅由当前页面提供，不写入持久存储。 */
export class Api {
  constructor(
    private key: string,
    private unauthorized: () => void = () => {},
  ) {}
  /** 请求真实管理接口。@throws ApiError 当服务器拒绝请求或返回非 JSON 响应。 */
  async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
    keepalive = false,
  ): Promise<T> {
    const response = await fetch(`/admin${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.key}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      cache: "no-store",
      keepalive,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) this.unauthorized();
      const detail = data?.error?.message ?? data?.detail ?? data?.message;
      throw new ApiError(
        response.status,
        typeof detail === "string"
          ? detail
          : response.status === 501
            ? "当前驱动不支持此功能，请切换真实浏览器驱动。"
            : `请求失败（${response.status}）`,
      );
    }
    return data as T;
  }
  /** 请求纯文本管理接口（如 /admin/metrics Prometheus 暴露格式）。 */
  async requestText(path: string, signal?: AbortSignal): Promise<string> {
    const response = await fetch(`/admin${path}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${this.key}` },
      signal,
      cache: "no-store",
    });
    if (!response.ok) {
      if (response.status === 401) this.unauthorized();
      throw new ApiError(response.status, `请求失败（${response.status}）`);
    }
    return response.text();
  }
}
/** 账号表的公开字段，不接收或展示 Cookie 明文。 */
export interface Account {
  id: string;
  label: string;
  enabled: boolean;
  status: string;
  effective_status?: string;
  inflight: number;
  max_concurrency?: number | null;
  effective_max_concurrency: number;
  credential_version?: number;
  created_at?: number;
  success_count: number;
  fail_count: number;
  last_used_at: number | null;
  last_error?: string | null;
  last_error_code?: string | null;
  last_error_at?: number | null;
  cooldown_until?: number;
  cookies?: Record<string, string>;
  meta?: {
    quota?: {
      checked_at?: number;
      remaining?: number;
      total?: number;
      used?: number;
      limit?: number;
      credits?: number;
      reset_at?: number;
      plan?: string;
    };
  };
}
/** 统计响应直接对应后端窗口聚合。 */
export interface Stats {
  total: number;
  errors: number;
  server_errors?: number;
  error_rate: number;
  latency_ms: { p50: number | null; p95: number | null };
  series: { t: number; count: number; errors: number }[];
  by_model: { model: string | null; count: number; errors: number }[];
  by_account?: { account_id: string | null; count: number; errors: number }[];
  by_key?: {
    key_id: string | null;
    key_name: string | null;
    count: number;
    errors: number;
  }[];
  by_status?: { status_code: number; count: number; errors: number }[];
}
/** 批量导入预览与执行使用相同结果结构。 */
export interface ImportResult {
  data: {
    index: number;
    label: string;
    status: string;
    message?: string;
    account_id?: string;
  }[];
  summary: {
    imported: number;
    ready: number;
    duplicate: number;
    error: number;
  };
}
/** 远程登录会话不包含上游凭据。 */
export interface LoginSession {
  id: string;
  status: string;
  expires_at: number | string;
  width: number;
  height: number;
}
/** 时间戳接受 API 的秒级数字与 ISO 字符串。 */
export function time(value: number | string | null | undefined) {
  if (!value) return "从未";
  return new Date(
    typeof value === "number" ? value * 1000 : value,
  ).toLocaleString("zh-CN", { hour12: false });
}
/** 图片显示坐标映射到上游浏览器真实视口。 */
export function framePoint(
  x: number,
  y: number,
  rect: { left: number; top: number; width: number; height: number },
  width: number,
  height: number,
) {
  return {
    x: Math.max(
      0,
      Math.min(width - 1, Math.floor(((x - rect.left) / rect.width) * width)),
    ),
    y: Math.max(
      0,
      Math.min(height - 1, Math.floor(((y - rect.top) / rect.height) * height)),
    ),
  };
}
