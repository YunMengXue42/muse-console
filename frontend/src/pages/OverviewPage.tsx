import type { Account, Stats } from "../api";
import { TrendChart } from "./TrendChart";
import type { Setter } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";
import "../console-pages.css";

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function OverviewPage({
  stats,
  windowValue,
  setWindowValue,
  accounts = [],
  onFilterRequests,
}: {
  stats: Stats | undefined;
  windowValue: string;
  setWindowValue: Setter<string>;
  accounts?: Account[];
  onFilterRequests?: (opts: { accountId?: string; keyId?: string; status?: string }) => void;
}) {
  const accountLabels = new Map(accounts.map((a) => [a.id, a.label]));
  const byAccount = stats?.by_account ?? [];
  const byKey = stats?.by_key ?? [];
  const byStatus = stats?.by_status ?? [];

  return (
    <div className="console-page overview-page">
      <div className="toolbar section-controls">
        <span className="muted">统计窗口</span>
        {[
          ["1h", "近 1 小时"],
          ["24h", "近 24 小时"],
          ["7d", "近 7 天"],
        ].map(([v, t]) => (
          <button
            className={windowValue === v ? "primary" : ""}
            aria-pressed={windowValue === v}
            key={v}
            onClick={() => setWindowValue(v)}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="metrics">
        {[
          ["总请求", stats?.total, "不含任务轮询请求"],
          [
            "失败请求",
            stats?.errors,
            stats?.server_errors != null
              ? `5xx 错误 ${stats.server_errors} 次`
              : "不含任务轮询请求",
          ],
          [
            "错误率",
            stats ? `${(stats.error_rate * 100).toFixed(1)}%` : undefined,
            "包含 HTTP 4xx/5xx 与失败任务",
          ],
          [
            "P95 延迟",
            stats?.latency_ms.p95 == null ? "—" : `${stats.latency_ms.p95} ms`,
            `P50 ${stats?.latency_ms.p50 ?? "—"} ms`,
          ],
        ].map(([t, v, sub]) => (
          <section key={String(t)}>
            <span>{t}</span>
            <strong>{v ?? "—"}</strong>
            <small>{sub}</small>
          </section>
        ))}
      </div>
      <section className="panel">
        <header>
          <h2>调用趋势</h2>
        </header>
        {stats?.series.length && stats.total > 0 ? (
          <TrendChart series={stats.series} windowValue={windowValue} />
        ) : (
          <Empty text="当前窗口暂无调用" />
        )}
      </section>
      <div className="overview-grid spaced">
        <section className="panel">
          <header>
            <h2>模型使用分布</h2>
          </header>
          {stats?.by_model.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>模型</th>
                    <th className="console-number">请求数</th>
                    <th className="console-number">错误数</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_model.map((m) => (
                    <tr key={m.model ?? "unknown"}>
                      <td data-label="模型">{m.model ?? "未指定模型"}</td>
                      <td data-label="请求数" className="console-number">
                        {m.count}
                      </td>
                      <td data-label="错误数" className="console-number">
                        {m.errors}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty text="暂无模型记录" />
          )}
        </section>
        <section className="panel">
          <header>
            <h2>账号调度分布</h2>
          </header>
          {byAccount.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>上游账号</th>
                    <th className="console-number">请求数</th>
                    <th className="console-number">错误数</th>
                  </tr>
                </thead>
                <tbody>
                  {byAccount.map((item) => {
                    const id = item.account_id;
                    const label = id ? accountLabels.get(id) : undefined;
                    return (
                      <tr key={id ?? "unassigned"}>
                        <td data-label="上游账号">
                          <div className="console-identity">
                            {id && onFilterRequests ? (
                              <button
                                type="button"
                                className="link-button"
                                onClick={() =>
                                  onFilterRequests({ accountId: id })
                                }
                              >
                                {label || id}
                              </button>
                            ) : (
                              <strong>{label || id || "未分配账号"}</strong>
                            )}
                            {label && id && <small className="mono">{id}</small>}
                          </div>
                        </td>
                        <td data-label="请求数" className="console-number">
                          {item.count}
                        </td>
                        <td data-label="错误数" className="console-number">
                          {item.errors}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty text="暂无账号调用记录" />
          )}
        </section>
        <section className="panel">
          <header>
            <h2>密钥调用排行</h2>
          </header>
          {byKey.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>应用密钥</th>
                    <th className="console-number">请求数</th>
                    <th className="console-number">错误数</th>
                  </tr>
                </thead>
                <tbody>
                  {byKey.map((item, idx) => (
                    <tr key={`${item.key_id ?? "anon"}-${idx}`}>
                      <td data-label="应用密钥">
                        <div className="console-identity">
                          {item.key_id && onFilterRequests ? (
                            <button
                              type="button"
                              className="link-button"
                              onClick={() =>
                                onFilterRequests({
                                  keyId: item.key_id ?? undefined,
                                })
                              }
                            >
                              {item.key_name || item.key_id}
                            </button>
                          ) : (
                            <strong>
                              {item.key_name || item.key_id || "未记录身份"}
                            </strong>
                          )}
                          {item.key_name &&
                            item.key_id &&
                            item.key_name !== item.key_id && (
                              <small className="mono">{item.key_id}</small>
                            )}
                        </div>
                      </td>
                      <td data-label="请求数" className="console-number">
                        {item.count}
                      </td>
                      <td data-label="错误数" className="console-number">
                        {item.errors}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty text="暂无密钥调用记录" />
          )}
        </section>
        <section className="panel">
          <header>
            <h2>状态码分布</h2>
          </header>
          {byStatus.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>HTTP 状态</th>
                    <th className="console-number">请求数</th>
                    <th className="console-number">失败计入</th>
                  </tr>
                </thead>
                <tbody>
                  {byStatus.map((item) => (
                    <tr key={item.status_code}>
                      <td data-label="HTTP 状态">
                        {badge(String(item.status_code))}
                      </td>
                      <td data-label="请求数" className="console-number">
                        {item.count}
                      </td>
                      <td data-label="失败计入" className="console-number">
                        {item.errors}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty text="暂无状态码记录" />
          )}
        </section>
      </div>
    </div>
  );
}
