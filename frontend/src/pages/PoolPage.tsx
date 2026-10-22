import { useEffect, useState } from "react";
import "../pool-design.css";
import type { Account } from "../api";
import { Api, time } from "../api";
import type { Pool, Setter, Act, ModalKind, Ask, Status } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";

function formatRemaining(untilSec?: number, nowMs = Date.now()): string | null {
  if (!untilSec) return null;
  const diffSec = Math.ceil(untilSec - nowMs / 1000);
  if (diffSec <= 0) return null;
  if (diffSec < 60) return `${diffSec}s 后恢复`;
  const m = Math.floor(diffSec / 60);
  const s = diffSec % 60;
  return s ? `${m}m ${s}s 后恢复` : `${m}m 后恢复`;
}

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function PoolPage({
  metrics,
  accounts,
  pool,
  search,
  setSearch,
  busy,
  act,
  api,
  setEdit,
  setLabel,
  setCookies,
  setMaxConcurrency,
  setMergeSource,
  capabilities,
  openModal,
  setPage,
  ask,
}: {
  metrics: Record<string, number> | undefined;
  accounts: Account[];
  pool: Pool | undefined;
  search: string;
  setSearch: Setter<string>;
  busy: boolean;
  act: Act;
  api: Api;
  setEdit: Setter<Account | null>;
  setLabel: Setter<string>;
  setCookies: Setter<string>;
  setMaxConcurrency?: Setter<string>;
  setMergeSource?: Setter<Account | null>;
  capabilities?: Status["capabilities"];
  openModal: (kind: ModalKind) => void;
  setPage: Setter<number>;
  ask: Ask;
}) {
  const [filter, setFilter] = useState("all");
  const [now, setNow] = useState(() => Date.now());
  const hasCooling = accounts.some(
    (a) =>
      ["cooling", "cooldown"].includes(a.effective_status ?? a.status) &&
      (a.cooldown_until ?? 0) * 1000 > now,
  );
  useEffect(() => {
    if (!hasCooling) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasCooling]);

  const accountStatus = (account: Account) =>
    account.effective_status ?? account.status;
  const available = (account: Account) =>
    ["active", "available"].includes(accountStatus(account));
  const attention = (account: Account) =>
    ["invalid", "cooling", "cooldown", "disabled"].includes(
      accountStatus(account),
    );
  const filters = [
    { value: "all", label: "全部", count: accounts.length },
    {
      value: "available",
      label: "可用",
      count: accounts.filter(available).length,
    },
    {
      value: "attention",
      label: "需关注",
      count: accounts.filter(attention).length,
    },
  ];
  const visibleAccounts = accounts.filter(
    (account) =>
      `${account.label} ${account.id}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()) &&
      (filter === "all" ||
        (filter === "available" ? available(account) : attention(account))),
  );

  return (
    <div className="pool-page">
      <div className="pool-metrics" aria-label="账号池概览">
        {[
          ["账号总数", metrics?.total, "已接入的上游会话"],
          ["可调度", metrics?.available, "可以接收新请求"],
          [
            "占用槽位",
            metrics?.inflight,
            metrics?.waiting
              ? `正在执行 · ${metrics.waiting} 个请求排队中`
              : "正在执行的并发请求",
          ],
          [
            "需要关注",
            metrics
              ? (metrics.invalid ?? 0) +
                (metrics.cooling ?? 0) +
                (metrics.disabled ?? 0)
              : undefined,
            "失效、冷却或停用",
          ],
        ].map(([title, value, subtitle]) => (
          <section key={String(title)}>
            <span>{title}</span>
            <strong>{value ?? "—"}</strong>
            <small>{subtitle}</small>
          </section>
        ))}
      </div>
      <div className="pool-layout">
        <section className="panel pool-accounts" aria-label="上游账号列表">
          <header>
            <div>
              <h2>上游账号</h2>
              <p className="muted">就绪会话自动参与调度。</p>
            </div>
            <span className="badge">{accounts.length} 个账号</span>
          </header>
          <div className="pool-toolbar">
            <div
              className="pool-filters"
              role="group"
              aria-label="账号状态筛选"
            >
              {filters.map((item) => (
                <button
                  key={item.value}
                  aria-pressed={filter === item.value}
                  onClick={() => setFilter(item.value)}
                >
                  {item.label} <span>{item.count}</span>
                </button>
              ))}
            </div>
            <div className="table-tools">
              <input
                aria-label="搜索账号"
                placeholder="搜索名称或账号 ID"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <label className="inline">
                调度策略
                <select
                  aria-label="调度策略"
                  value={pool?.strategy ?? "round_robin"}
                  disabled={busy || !pool}
                  onChange={(e) =>
                    void act(async () => {
                      await api.request("/pool", "PATCH", {
                        strategy: e.target.value,
                      });
                    }, "调度策略已更新")
                  }
                >
                  <option value="round_robin">轮询</option>
                  <option value="lru">最少最近使用</option>
                  <option value="affinity">会话亲和</option>
                </select>
              </label>
            </div>
          </div>
          {accounts.length ? (
            <div
              className="pool-table"
              tabIndex={0}
              role="region"
              aria-label="账号表格，可横向滚动"
            >
              <table aria-label="上游账号">
                <thead>
                  <tr>
                    {[
                      "账号标识",
                      "状态",
                      "并发",
                      "成功 / 失败",
                      "最近使用",
                      "操作",
                    ].map((t) => (
                      <th key={t}>{t}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleAccounts.map((a) => {
                    const effStatus = a.effective_status ?? a.status;
                    const cooldownText = ["cooling", "cooldown"].includes(
                      effStatus,
                    )
                      ? formatRemaining(a.cooldown_until, now)
                      : null;
                    const quota = a.meta?.quota;
                    return (
                      <tr key={a.id}>
                        <td data-label="账号标识">
                          <div className="pool-identity">
                            <div>
                              <strong>{a.label || "未命名账号"}</strong>
                              <small className="mono">
                                {a.id}
                                {a.credential_version
                                  ? ` · v${a.credential_version}`
                                  : ""}
                              </small>
                              {quota && (
                                <small className="muted">
                                  {quota.plan ? `${quota.plan} · ` : ""}
                                  {quota.remaining != null
                                    ? `余量 ${quota.remaining}`
                                    : quota.credits != null
                                      ? `积分 ${quota.credits}`
                                      : ""}
                                </small>
                              )}
                              {a.last_error && (
                                <small className="danger-text">
                                  {a.last_error}
                                </small>
                              )}
                            </div>
                          </div>
                        </td>
                        <td data-label="状态">
                          <div className="console-identity">
                            {badge(effStatus)}
                            {cooldownText && (
                              <small className="mono muted">
                                {cooldownText}
                              </small>
                            )}
                          </div>
                        </td>
                        <td data-label="并发" className="mono">
                          {a.inflight ?? 0} /{" "}
                          {a.effective_max_concurrency ?? "—"}
                          {a.max_concurrency != null && (
                            <small className="muted">独立上限</small>
                          )}
                        </td>
                        <td data-label="成功 / 失败" className="mono">
                          <span className="pool-counts">
                            {a.success_count}{" "}
                            <span className="muted">/ {a.fail_count}</span>
                          </span>
                        </td>
                        <td data-label="最近使用">{time(a.last_used_at)}</td>
                        <td data-label="操作">
                          <div className="row-actions">
                            <button
                              onClick={() => {
                                setEdit(a);
                                setLabel(a.label);
                                setCookies("");
                                setMaxConcurrency?.(
                                  a.max_concurrency != null
                                    ? String(a.max_concurrency)
                                    : "",
                                );
                              }}
                            >
                              编辑
                            </button>
                            <button
                              disabled={busy}
                              onClick={() =>
                                void act(
                                  async () => {
                                    await api.request(
                                      `/accounts/${a.id}`,
                                      "PATCH",
                                      { enabled: !a.enabled },
                                    );
                                  },
                                  a.enabled ? "账号已停用" : "账号已启用",
                                )
                              }
                            >
                              {a.enabled ? "停用" : "启用"}
                            </button>
                            <button
                              disabled={
                                busy || capabilities?.renew_session === false
                              }
                              title={
                                capabilities?.renew_session === false
                                  ? "当前驱动不支持会话保活"
                                  : undefined
                              }
                              onClick={() =>
                                void act(async () => {
                                  await api.request(
                                    `/accounts/${a.id}/renew`,
                                    "POST",
                                  );
                                }, "保活请求已完成")
                              }
                            >
                              保活
                            </button>
                            <button
                              disabled={busy}
                              onClick={() =>
                                void act(async () => {
                                  await api.request(
                                    `/accounts/${a.id}/reset`,
                                    "POST",
                                  );
                                }, "状态已重置")
                              }
                            >
                              重置
                            </button>
                            {setMergeSource && accounts.length > 1 && (
                              <button
                                disabled={busy}
                                onClick={() => setMergeSource(a)}
                              >
                                合并
                              </button>
                            )}
                            <button
                              className="danger-text"
                              onClick={() =>
                                ask(
                                  `删除账号「${a.label || a.id}」？此操作不可撤销。`,
                                  () =>
                                    api.request(`/accounts/${a.id}`, "DELETE"),
                                )
                              }
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!visibleAccounts.length && (
                <div role="status">
                  <Empty text="没有匹配的账号">
                    <p className="muted">更换名称、ID 或清除筛选。</p>
                    <button
                      onClick={() => {
                        setSearch("");
                        setFilter("all");
                      }}
                    >
                      清除筛选
                    </button>
                  </Empty>
                </div>
              )}
            </div>
          ) : (
            <Empty text="暂无账号">
              <button className="primary" onClick={() => openModal("login")}>
                登录上游账号
              </button>
            </Empty>
          )}
          <div className="panel-foot">
            <span aria-live="polite">
              显示 {visibleAccounts.length} / {accounts.length} 个账号
            </span>
            <span>
              {pool
                ? `并发上限 ${pool.max_concurrency} · 冷却 ${pool.cooldown}s · 获取超时 ${pool.acquire_timeout}s`
                : "调度配置等待同步"}
            </span>
          </div>
        </section>
        <details className="pool-guide">
          <summary>
            接入说明 <span>连接账号 → 验证会话 → 分配密钥</span>
          </summary>
          <div className="pool-guide-steps">
            {[
              ["连接上游账号", "逐号网页登录，或批量导入 Cookie。"],
              ["验证会话状态", "可调度后即可使用；验证码需手动完成。"],
              ["分配调用密钥", "为应用创建独立 API Key。"],
            ].map(([title, body], i) => (
              <div className="step" key={title}>
                <span>{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="pool-guide-foot">
            <button onClick={() => setPage(4)}>管理 API 密钥 →</button>
            <p className="fine">
              仅接入有权使用的账号；Cookie 是敏感凭据，勿公开分享。
            </p>
          </div>
        </details>
      </div>
    </div>
  );
}
