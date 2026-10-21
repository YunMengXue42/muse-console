export const PAGE_SLUGS = [
  "pool",
  "overview",
  "tasks",
  "requests",
  "keys",
] as const;

export interface RouteState {
  page: number;
  windowValue?: string;
  filter?: string;
  accountFilter?: string;
  pathFilter?: string;
  keyFilter?: string;
  hidePolls?: boolean;
}

/** 解析 URL Hash 中的页面与筛选状态（仅白名单枚举与安全字符）。 */
export function parseConsoleHash(rawHash: string): RouteState {
  const cleaned = rawHash.replace(/^#\/?/, "").trim();
  if (!cleaned) return { page: 0 };
  const [slugPart, queryPart = ""] = cleaned.split("?", 2);
  const slugIndex = PAGE_SLUGS.indexOf(slugPart as (typeof PAGE_SLUGS)[number]);
  const page = slugIndex >= 0 ? slugIndex : 0;
  const params = new URLSearchParams(queryPart);
  const result: RouteState = { page };
  const win = params.get("window");
  if (win && ["1h", "24h", "7d"].includes(win)) {
    result.windowValue = win;
  }
  const st = params.get("status");
  if (st !== null && ["", "failed", "2xx", "3xx", "4xx", "5xx"].includes(st)) {
    result.filter = st;
  }
  const acc = params.get("account_id");
  if (acc !== null) result.accountFilter = acc;
  const path = params.get("path");
  if (path !== null) result.pathFilter = path;
  const keyId = params.get("key_id");
  if (keyId !== null) result.keyFilter = keyId;
  const polls = params.get("hide_polls");
  if (polls === "true" || polls === "false") {
    result.hidePolls = polls === "true";
  }
  return result;
}

/** 将当前页签与筛选参数编码为简洁的 URL Hash。 */
export function formatConsoleHash(state: {
  page: number;
  windowValue: string;
  filter: string;
  accountFilter: string;
  pathFilter: string;
  keyFilter: string;
  hidePolls: boolean;
}): string {
  const slug = PAGE_SLUGS[state.page] ?? "pool";
  const params = new URLSearchParams();
  if (state.page === 1 && state.windowValue !== "24h") {
    params.set("window", state.windowValue);
  }
  if (state.page === 3) {
    if (state.filter) params.set("status", state.filter);
    if (state.accountFilter) params.set("account_id", state.accountFilter);
    if (state.pathFilter) params.set("path", state.pathFilter);
    if (state.keyFilter) params.set("key_id", state.keyFilter);
    if (!state.hidePolls) params.set("hide_polls", "false");
  }
  const qs = params.toString();
  return qs ? `#${slug}?${qs}` : `#${slug}`;
}

export function printable(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export function sameOriginMediaUrl(raw: string): string | null {
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.pathname.startsWith("/v1/media/")) {
      return `${parsed.pathname}${parsed.search}`;
    }
  } catch {
    if (raw.startsWith("/v1/media/")) return raw;
  }
  return null;
}

export function extractMediaPreview(
  body: unknown,
): { url: string; kind: "image" | "video"; caption?: string }[] {
  if (!body || typeof body !== "object") return [];
  const root = body as { result?: unknown };
  const target =
    root.result && typeof root.result === "object" ? root.result : body;
  if (!target || typeof target !== "object") return [];
  const obj = target as {
    url?: unknown;
    mime?: unknown;
    data?: unknown;
  };
  const items: { url: string; kind: "image" | "video"; caption?: string }[] =
    [];
  if (typeof obj.url === "string") {
    const safe = sameOriginMediaUrl(obj.url);
    if (safe) {
      const isVideo =
        (typeof obj.mime === "string" && obj.mime.startsWith("video/")) ||
        /\.(mp4|webm|mov)(\?|$)/i.test(safe) ||
        safe.includes("/vid_");
      items.push({ url: safe, kind: isVideo ? "video" : "image" });
    }
  }
  if (Array.isArray(obj.data)) {
    for (const entry of obj.data) {
      if (entry && typeof entry === "object") {
        const rec = entry as { url?: unknown; revised_prompt?: unknown };
        if (typeof rec.url === "string") {
          const safe = sameOriginMediaUrl(rec.url);
          if (safe) {
            items.push({
              url: safe,
              kind: "image",
              caption:
                typeof rec.revised_prompt === "string"
                  ? rec.revised_prompt
                  : undefined,
            });
          }
        }
      }
    }
  }
  return items;
}

export interface ParsedMetrics {
  raw: string;
  mediaOutcomes: { kind: string; outcome: string; count: number }[];
  upstreamRequests: {
    account: string;
    op: string;
    result: string;
    count: number;
  }[];
  firstToken: {
    totalCount: number;
    totalSum: number;
    avgSeconds: number | null;
    byAccount: {
      account: string;
      count: number;
      sum: number;
      avgSeconds: number;
    }[];
  };
  generations: {
    kind: string;
    result: string;
    count: number;
    sum: number;
    avgSeconds: number;
  }[];
}

function parsePromLabels(
  rawLabels: string | undefined,
): Record<string, string> {
  if (!rawLabels) return {};
  const result: Record<string, string> = {};
  const re = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(rawLabels)) !== null) {
    result[m[1]] = m[2].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
  return result;
}

/** 解析后端 0.3.5 /admin/metrics 输出的 Prometheus 文本指标。 */
export function parsePrometheusMetrics(text: string): ParsedMetrics {
  const mediaOutcomes: ParsedMetrics["mediaOutcomes"] = [];
  const upstreamRequests: ParsedMetrics["upstreamRequests"] = [];
  const ftMap = new Map<string, { sum: number; count: number }>();
  const genMap = new Map<
    string,
    { kind: string; result: string; sum: number; count: number }
  >();

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match =
      /^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{((?:[^"\\]|\\.)*"(?:[^"\\]|\\.)*"[^}]*)\})?\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$/.exec(
        line,
      );
    if (!match) continue;
    const [, name, labelStr, valueStr] = match;
    const value = Number(valueStr);
    if (!Number.isFinite(value)) continue;
    const labels = parsePromLabels(labelStr);

    if (name === "muse_media_outcomes_total") {
      mediaOutcomes.push({
        kind: labels.kind ?? "unknown",
        outcome: labels.outcome ?? "unknown",
        count: value,
      });
    } else if (name === "muse_upstream_requests_total") {
      upstreamRequests.push({
        account: labels.account ?? "unknown",
        op: labels.op ?? "unknown",
        result: labels.result ?? "unknown",
        count: value,
      });
    } else if (name === "muse_chat_first_token_seconds_sum") {
      const acc = labels.account ?? "unknown";
      const cur = ftMap.get(acc) ?? { sum: 0, count: 0 };
      cur.sum = value;
      ftMap.set(acc, cur);
    } else if (name === "muse_chat_first_token_seconds_count") {
      const acc = labels.account ?? "unknown";
      const cur = ftMap.get(acc) ?? { sum: 0, count: 0 };
      cur.count = value;
      ftMap.set(acc, cur);
    } else if (name === "muse_generation_seconds_sum") {
      const kind = labels.kind ?? "unknown";
      const res = labels.result ?? "unknown";
      const key = `${kind}:${res}`;
      const cur = genMap.get(key) ?? { kind, result: res, sum: 0, count: 0 };
      cur.sum = value;
      genMap.set(key, cur);
    } else if (name === "muse_generation_seconds_count") {
      const kind = labels.kind ?? "unknown";
      const res = labels.result ?? "unknown";
      const key = `${kind}:${res}`;
      const cur = genMap.get(key) ?? { kind, result: res, sum: 0, count: 0 };
      cur.count = value;
      genMap.set(key, cur);
    }
  }

  let ftTotalCount = 0;
  let ftTotalSum = 0;
  const byAccount: ParsedMetrics["firstToken"]["byAccount"] = [];
  for (const [account, { sum, count }] of ftMap.entries()) {
    if (count > 0) {
      ftTotalCount += count;
      ftTotalSum += sum;
      byAccount.push({
        account,
        count,
        sum,
        avgSeconds: sum / count,
      });
    }
  }

  const generations: ParsedMetrics["generations"] = [];
  for (const item of genMap.values()) {
    if (item.count > 0) {
      generations.push({
        ...item,
        avgSeconds: item.sum / item.count,
      });
    }
  }

  return {
    raw: text,
    mediaOutcomes,
    upstreamRequests,
    firstToken: {
      totalCount: ftTotalCount,
      totalSum: ftTotalSum,
      avgSeconds: ftTotalCount > 0 ? ftTotalSum / ftTotalCount : null,
      byAccount,
    },
    generations,
  };
}
