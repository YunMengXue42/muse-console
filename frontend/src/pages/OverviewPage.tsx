import type { Account, Stats } from "../api";
import { TrendChart } from "./TrendChart";
import type { Detail, Setter } from "../types";
import type { ParsedMetrics } from "../console-utils";
import { Empty } from "../ui";
import { badge } from "../status";
import "../console-pages.css";

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function OverviewPage({
  stats,
  windowValue,
  setWindowValue,
  accounts = [],
  promMetrics,
  setDetail,
  onFilterRequests,
}: {
  stats: Stats | undefined;
  windowValue: string;
  setWindowValue: Setter<string>;
  accounts?: Account[];
  promMetrics?: ParsedMetrics;
  setDetail?: Setter<Detail | null>;
  onFilterRequests?: (opts: {
    accountId?: string;
    keyId?: string;
    status?: string;
  }) => void;
}) {
  const accountLabels = new Map(accounts.map((a) => [a.id, a.label]));
  const byAccount = stats?.by_account ?? [];
  const byKey = stats?.by_key ?? [];
  const byStatus = stats?.by_status ?? [];
  const mediaOutcomes = promMetrics?.mediaOutcomes ?? [];
  const upstreamRequests = promMetrics?.upstreamRequests ?? [];
  const firstToken = promMetrics?.firstToken;
  const generations = promMetrics?.generations ?? [];
  const hasPromData = Boolean(
    (firstToken && firstToken.totalCount > 0) ||
    generations.length > 0 ||
    mediaOutcomes.length > 0 ||
    upstreamRequests.length > 0,
  );
  const imageOkGen = generations.find(
    (g) => g.kind === "image" && g.result === "ok",
  );
  const videoOkGen = generations.find(
    (g) => g.kind === "video" && g.result === "ok",
  );
  const textOnlyRetries = mediaOutcomes
    .filter((m) => m.outcome === "retry_text_only")
    .reduce((acc, m) => acc + m.count, 0);
  const retrySuccesses = mediaOutcomes
    .filter((m) => m.outcome === "succeeded_after_retry")
    .reduce((acc, m) => acc + m.count, 0);

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
        {promMetrics?.raw && setDetail && (
          <button
            type="button"
            className="console-action prom-raw-button"
            onClick={() =>
              setDetail({
                title: "Prometheus 指标 (/admin/metrics)",
                body: promMetrics.raw,
              })
            }
          >
            查看 Prometheus 指标
          </button>
        )}
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
                            {label && id && (
                              <small className="mono">{id}</small>
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
      {hasPromData && (
        <>
          <div className="metrics spaced" aria-label="进程内实时指标">
            {[
              [
                "Chat 平均首字耗时",
                firstToken?.avgSeconds != null
                  ? `${firstToken.avgSeconds.toFixed(2)} s`
                  : "—",
                firstToken?.totalCount
                  ? `累计 ${firstToken.totalCount} 次流式首字采样`
                  : "进程启动后尚无采样",
              ],
              [
                "图像生成均耗",
                imageOkGen ? `${imageOkGen.avgSeconds.toFixed(1)} s` : "—",
                imageOkGen
                  ? `成功 ${imageOkGen.count} 次`
                  : "进程启动后尚无采样",
              ],
              [
                "视频生成均耗",
                videoOkGen ? `${videoOkGen.avgSeconds.toFixed(1)} s` : "—",
                videoOkGen
                  ? `成功 ${videoOkGen.count} 次`
                  : "进程启动后尚无采样",
              ],
              [
                "只回文本自动重投",
                textOnlyRetries,
                retrySuccesses > 0
                  ? `重投后成功 ${retrySuccesses} 次`
                  : "MediaWait 状态机累计触发",
              ],
            ].map(([t, v, sub]) => (
              <section key={String(t)}>
                <span>{t}</span>
                <strong>{v}</strong>
                <small>{sub}</small>
              </section>
            ))}
          </div>
          <div className="overview-grid spaced">
            <section className="panel">
              <header>
                <h2>媒体等待状态机（MediaWait）</h2>
                <span className="muted">进程内累计</span>
              </header>
              {mediaOutcomes.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>类型</th>
                        <th>状态机结果</th>
                        <th className="console-number">次数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mediaOutcomes.map((item) => (
                        <tr key={`${item.kind}-${item.outcome}`}>
                          <td data-label="类型">{item.kind}</td>
                          <td data-label="状态机结果">
                            <div className="console-identity">
                              {badge(item.outcome)}
                              <small className="mono">{item.outcome}</small>
                            </div>
                          </td>
                          <td data-label="次数" className="console-number">
                            {item.count}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty text="暂无媒体状态机事件" />
              )}
            </section>
            <section className="panel">
              <header>
                <h2>网关上游调用细分</h2>
                <span className="muted">按操作与错误码</span>
              </header>
              {upstreamRequests.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>账号 / 操作</th>
                        <th>结果码</th>
                        <th className="console-number">次数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {upstreamRequests.map((item, idx) => {
                        const label = accountLabels.get(item.account);
                        return (
                          <tr
                            key={`${item.account}-${item.op}-${item.result}-${idx}`}
                          >
                            <td data-label="账号 / 操作">
                              <div className="console-identity">
                                <strong>{label || item.account}</strong>
                                <small className="mono">{item.op}</small>
                              </div>
                            </td>
                            <td data-label="结果码">{badge(item.result)}</td>
                            <td data-label="次数" className="console-number">
                              {item.count}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty text="暂无网关上游记录" />
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
