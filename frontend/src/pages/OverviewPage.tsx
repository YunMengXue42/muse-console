import { time, type Stats } from "../api";
import type { Setter } from "../types";
import { Empty } from "../ui";

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function OverviewPage({
  stats,
  windowValue,
  setWindowValue,
}: {
  stats: Stats | undefined;
  windowValue: string;
  setWindowValue: Setter<string>;
}) {
  return (
    <>
      <div className="toolbar section-controls">
        <span className="muted">统计窗口</span>
        {[
          ["1h", "近 1 小时"],
          ["24h", "近 24 小时"],
          ["7d", "近 7 天"],
        ].map(([v, t]) => (
          <button
            className={windowValue === v ? "primary" : ""}
            key={v}
            onClick={() => setWindowValue(v)}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="metrics">
        {[
          ["总请求", stats?.total],
          ["失败请求", stats?.errors],
          [
            "错误率",
            stats ? `${(stats.error_rate * 100).toFixed(1)}%` : undefined,
          ],
          [
            "P95 延迟",
            stats?.latency_ms.p95 == null ? "—" : `${stats.latency_ms.p95} ms`,
          ],
        ].map(([t, v]) => (
          <section key={t}>
            <span>{t}</span>
            <strong>{v ?? "—"}</strong>
            <small>
              {t === "P95 延迟"
                ? `P50 ${stats?.latency_ms.p50 ?? "—"} ms`
                : "不含任务轮询请求"}
            </small>
          </section>
        ))}
      </div>
      <section className="panel">
        <header>
          <h2>调用趋势</h2>
          <span className="muted">蓝色：请求 / 深灰：错误</span>
        </header>
        {stats?.series.length && stats.total > 0 ? (
          <>
            <div className="chart" role="img" aria-label="请求与错误数量趋势">
              {stats.series.map((point) => (
                <div
                  key={point.t}
                  className="chart-column"
                  title={`${time(point.t)} · ${point.count} 次请求 / ${point.errors} 次错误`}
                >
                  <div
                    className="chart-bar"
                    style={{
                      height: `${(point.count / Math.max(...stats.series.map((p) => p.count), 1)) * 100}%`,
                    }}
                  />
                  <div
                    className="chart-error"
                    style={{
                      height: `${(point.errors / Math.max(...stats.series.map((p) => p.count), 1)) * 100}%`,
                    }}
                  />
                </div>
              ))}
            </div>
            <div className="chart-axis">
              <span>{time(stats.series[0].t)}</span>
              <span>{time(stats.series[stats.series.length - 1].t)}</span>
            </div>
          </>
        ) : (
          <Empty text="当前窗口暂无调用" />
        )}
      </section>
      <section className="panel spaced">
        <header>
          <h2>模型使用分布</h2>
        </header>
        {stats?.by_model.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>模型</th>
                  <th>请求数</th>
                  <th>错误数</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_model.map((m) => (
                  <tr key={m.model ?? "unknown"}>
                    <td>{m.model ?? "未指定模型"}</td>
                    <td>{m.count}</td>
                    <td>{m.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty text="暂无模型记录" />
        )}
      </section>
    </>
  );
}
