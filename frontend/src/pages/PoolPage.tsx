import type { Account } from "../api";
import { Api, time } from "../api";
import type { Pool, Setter, Act, ModalKind, Ask } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";

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
  openModal: (kind: ModalKind) => void;
  setPage: Setter<number>;
  ask: Ask;
}) {
  return (
    <>
      <div className="metrics">
        {[
          ["账号总数", metrics?.total, "已接入的上游会话"],
          ["可调度", metrics?.available, "可以接收新请求"],
          ["占用槽位", metrics?.inflight, "正在执行的并发请求"],
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
      <div className="account-layout">
        <section className="panel">
          <header>
            <div>
              <h2>上游账号</h2>
              <p className="muted">会话就绪后，自动参与请求调度。</p>
            </div>
            <span className="badge">{accounts.length} 个账号</span>
          </header>
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
          {accounts.length ? (
            <div className="table-wrap">
              <table>
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
                  {accounts
                    .filter((a) =>
                      `${a.label} ${a.id}`
                        .toLowerCase()
                        .includes(search.toLowerCase()),
                    )
                    .map((a) => (
                      <tr key={a.id}>
                        <td data-label="账号标识">
                          <strong>{a.label || "未命名账号"}</strong>
                          <small className="mono">{a.id}</small>
                          {a.last_error && (
                            <small className="danger-text">
                              {a.last_error}
                            </small>
                          )}
                        </td>
                        <td data-label="状态">
                          {badge(a.effective_status ?? a.status)}
                        </td>
                        <td data-label="并发" className="mono">
                          {a.inflight ?? 0} /{" "}
                          {a.effective_max_concurrency ?? "—"}
                        </td>
                        <td data-label="成功 / 失败" className="mono">
                          {a.success_count}{" "}
                          <span className="muted">/ {a.fail_count}</span>
                        </td>
                        <td data-label="最近使用">{time(a.last_used_at)}</td>
                        <td data-label="操作">
                          <div className="row-actions">
                            <button
                              onClick={() => {
                                setEdit(a);
                                setLabel(a.label);
                                setCookies("");
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
                              disabled={busy}
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
                    ))}
                </tbody>
              </table>
              {search &&
                !accounts.some((a) =>
                  `${a.label} ${a.id}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                ) && <Empty text="没有匹配的账号" />}
            </div>
          ) : (
            <Empty text="从第一个账号开始">
              <button className="primary" onClick={() => openModal("login")}>
                登录上游账号
              </button>
            </Empty>
          )}
          <div className="panel-foot">
            {pool
              ? `并发上限 ${pool.max_concurrency} · 冷却 ${pool.cooldown}s · 获取超时 ${pool.acquire_timeout}s`
              : "调度配置等待同步"}
          </div>
        </section>
        <aside className="guide">
          <span className="eyebrow">GET STARTED</span>
          <h2>
            连接。验证。
            <br />
            开始调用。
          </h2>
          {[
            [
              "连接上游账号",
              "逐号打开真实网页完成登录，或批量导入已有 Cookie。",
            ],
            [
              "验证会话状态",
              "账号显示可调度后即可使用。验证码需在登录页面手动完成。",
            ],
            [
              "分配调用密钥",
              "为你的应用创建独立 API Key，在调用概览追踪运行状况。",
            ],
          ].map(([title, body], i) => (
            <div className="step" key={title}>
              <span>{String(i + 1).padStart(2, "0")}</span>
              <div>
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            </div>
          ))}
          <button onClick={() => setPage(4)}>管理 API 密钥 →</button>
          <p className="fine">
            请仅接入你有权使用的账号。Cookie 属于敏感凭据，不要公开分享。
          </p>
        </aside>
      </div>
    </>
  );
}
