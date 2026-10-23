// Mirrored from upstream/muse.py::SESSION_COOKIES; do not expand without review.
export const NAMES = [
  "hatch_sess",
  "hatch_gw",
  "hatch_vml",
  "hatch_native_auth_device",
] as const;
// Mirrored from upstream/muse.py::REQUIRED_SESSION_COOKIES. muse.ai no longer issues hatch_gw,
// so only hatch_sess proves an authenticated session; the rest are forwarded when present.
export const REQUIRED_NAMES = ["hatch_sess"] as const;
export const MUSE = "https://muse.ai/";
export function originOf(input: string, development = false): string {
  const u = new URL(input);
  if (u.username || u.password || u.pathname !== "/" || u.search || u.hash)
    throw new Error("origin_only");
  // IPv6 loopback is excluded: Chrome rejects "http://[::1]:*" in extension CSP connect-src.
  const loopback = ["localhost", "127.0.0.1"].includes(u.hostname);
  if (
    u.protocol !== "https:" &&
    !(development && loopback && u.protocol === "http:")
  )
    throw new Error("https_required");
  return u.origin;
}
export function permission(origin: string): string {
  return `${origin}/*`;
}
export function museURL(url?: string): boolean {
  try {
    const u = new URL(url!);
    return u.origin === "https://muse.ai";
  } catch {
    return false;
  }
}
export function allowedCookie(c: chrome.cookies.Cookie): boolean {
  return (
    (NAMES as readonly string[]).includes(c.name) &&
    ["muse.ai", ".muse.ai"].includes(c.domain)
  );
}
export type Partition = { topLevelSite: string; hasCrossSiteAncestor: boolean };
export function partitionOf(
  p?: chrome.cookies.CookiePartitionKey,
): Partition | null {
  if (!p) return null;
  if (!p.topLevelSite || typeof p.hasCrossSiteAncestor !== "boolean")
    throw new Error("partition_unsupported");
  return {
    topLevelSite: p.topLevelSite,
    hasCrossSiteAncestor: p.hasCrossSiteAncestor,
  };
}
export function sourceKey(store: string, partition: Partition | null): string {
  return JSON.stringify([store, partition]);
}
export interface Envelope {
  schema_version: 1;
  source: "extension";
  device_id: string;
  capture_id: string;
  source_revision: number;
  captured_at: number;
  store_id: string;
  partition_key: Partition | null;
  base_credential_version: number;
  cookies: Array<{
    name: string;
    value: string;
    domain: string;
    path: string;
    secure: boolean;
    httpOnly: boolean;
    sameSite: chrome.cookies.SameSiteStatus;
    hostOnly: boolean;
    session: boolean;
    expires: number | null;
    partitionKey?: Partition;
  }>;
}
export interface Pairing {
  origin: string;
  development: boolean;
  device_id: string;
  token: string;
  server_id: string;
  expires_at: number;
}
export interface Result {
  id: string;
  status:
    | "committed"
    | "awaiting_login"
    | "rejected"
    | "retry_wait"
    | "needs_identity_confirmation"
    | "conflict";
  account_id?: string;
  credential_version?: number;
  code: string;
}
export async function request(
  origin: string,
  path: string,
  body?: unknown,
  token?: string,
): Promise<unknown> {
  const response = await fetch(origin + path, {
    method: body === undefined ? "GET" : "POST",
    redirect: "error",
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
    signal: AbortSignal.timeout(15000),
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok)
    throw new Error(
      response.status === 401 || response.status === 403
        ? "device_unauthorized"
        : response.status === 429 || response.status >= 500
          ? "temporary"
          : "request_rejected",
    );
  return response.json();
}
