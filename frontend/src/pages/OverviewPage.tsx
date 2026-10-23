import { t } from "../i18n";
import type { CSSProperties, ReactNode } from "react";
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
  // 按账号聚合上游调用，避免「账号 × 操作 × 结果」逐行铺开。
  const upstreamByAccount = (() => {
    const map = new Map<
      string,
      {
        account: string;
        total: number;
        failed: number;
        ops: Map<string, number>;
      }
    >();
    for (const item of upstreamRequests) {
      const row = map.get(item.account) ?? {
        account: item.account,
        total: 0,
        failed: 0,
        ops: new Map<string, number>(),
      };
      row.total += item.count;
      if (item.result !== "ok") row.failed += item.count;
      row.ops.set(item.op, (row.ops.get(item.op) ?? 0) + item.count);
      map.set(item.account, row);
    }
    return [...map.values()].sort((a, b) => b.total - a.total);
  })();
  const liveMetrics: [string, string | number, string][] = hasPromData
    ? [
        [
          "Chat 平均首字耗时",
          firstToken?.avgSeconds != null
            ? `${firstToken.avgSeconds.toFixed(2)} s`
            : "—",
          firstToken?.totalCount
            ? t("累计 {totalCount} 次流式首字采样", {
                totalCount: firstToken.totalCount,
              })
            : "进程启动后尚无采样",
        ],
        [
          "图像生成均耗",
          imageOkGen ? `${imageOkGen.avgSeconds.toFixed(1)} s` : "—",
          imageOkGen
            ? t("成功 {count} 次", { count: imageOkGen.count })
            : "进程启动后尚无采样",
        ],
        [
          "视频生成均耗",
          videoOkGen ? `${videoOkGen.avgSeconds.toFixed(1)} s` : "—",
          videoOkGen
            ? t("成功 {count} 次", { count: videoOkGen.count })
            : "进程启动后尚无采样",
        ],
        [
          "只回文本自动重投",
          textOnlyRetries,
          retrySuccesses > 0
            ? t("重投后成功 {retrySuccesses} 次", { retrySuccesses })
            : "MediaWait 状态机累计触发",
        ],
      ]
    : [];

  return (
    <div className="console-page overview-page">
      <div className="toolbar section-controls">
        <span className="muted">{t("统计窗口")}</span>
        {[
          ["1h", "近 1 小时"],
          ["24h", "近 24 小时"],
          ["7d", "近 7 天"],
        ].map(([v, label]) => (
          <button
            className={windowValue === v ? "primary" : ""}
            aria-pressed={windowValue === v}
            key={v}
            onClick={() => setWindowValue(v)}
          >
            {t(label)}
          </button>
        ))}
        {promMetrics?.raw && setDetail && (
          <button
            type="button"
            className="console-action prom-raw-button"
            onClick={() =>
              setDetail({
                title: t("Prometheus 指标 (/admin/metrics)"),
                body: promMetrics.raw,
              })
            }
          >
            {t("查看 Prometheus 指标")}
          </button>
        )}
      </div>
      <div
        className={`metrics overview-metrics${liveMetrics.length ? " has-live" : ""}`}
      >
        {[
          ["总请求", stats?.total, "不含任务轮询请求"],
          [
            "失败请求",
            stats?.errors,
            stats?.server_errors != null
              ? t("5xx 错误 {n} 次", { n: stats.server_errors })
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
          ...liveMetrics,
        ].map(([label, v, sub]) => (
          <section key={String(label)}>
            <span>{t(String(label))}</span>
            <strong>{v ?? "—"}</strong>
            <small>{t(String(sub))}</small>
          </section>
        ))}
      </div>
      <div className="overview-main">
        <section className="panel overview-trend">
          <header>
            <h2>{t("调用趋势")}</h2>
          </header>
          {stats?.series.length && stats.total > 0 ? (
            <TrendChart series={stats.series} windowValue={windowValue} />
          ) : (
            <Empty text={t("当前窗口暂无调用")} />
          )}
        </section>
        <section className="panel">
          <header>
            <h2>{t("模型")}</h2>
            <span className="muted">
              {t("{n} 个", { n: stats?.by_model.length ?? 0 })}
            </span>
          </header>
          <BarList
            empty={t("暂无模型记录")}
            rows={(stats?.by_model ?? []).map((m) => ({
              key: m.model ?? "unknown",
              label: m.model ?? t("未指定模型"),
              count: m.count,
              errors: m.errors,
            }))}
          />
        </section>
      </div>
      <div className={`overview-grid${hasPromData ? " has-live" : ""}`}>
        <section className="panel">
          <header>
            <h2>{t("账号调度")}</h2>
            <span className="muted">
              {t("{n} 个", { n: byAccount.length })}
            </span>
          </header>
          <BarList
            empty={t("暂无账号调用记录")}
            rows={byAccount.map((item) => {
              const id = item.account_id;
              const label = (id && accountLabels.get(id)) || id;
              return {
                key: id ?? "unassigned",
                label:
                  id && onFilterRequests ? (
                    <button
                      type="button"
                      className="link-button"
                      title={id}
                      onClick={() => onFilterRequests({ accountId: id })}
                    >
                      {label}
                    </button>
                  ) : (
                    (label ?? t("未分配账号"))
                  ),
                count: item.count,
                errors: item.errors,
              };
            })}
          />
        </section>
        <section className="panel">
          <header>
            <h2>{t("密钥排行")}</h2>
            <span className="muted">{t("{n} 个", { n: byKey.length })}</span>
          </header>
          <BarList
            empty={t("暂无密钥调用记录")}
            rows={byKey.map((item, idx) => {
              const name = item.key_name || item.key_id || t("未记录身份");
              return {
                key: `${item.key_id ?? "anon"}-${idx}`,
                label:
                  item.key_id && onFilterRequests ? (
                    <button
                      type="button"
                      className="link-button"
                      title={item.key_id}
                      onClick={() =>
                        onFilterRequests({ keyId: item.key_id ?? undefined })
                      }
                    >
                      {name}
                    </button>
                  ) : (
                    name
                  ),
                count: item.count,
                errors: item.errors,
              };
            })}
          />
        </section>
        <section className="panel">
          <header>
            <h2>{t("状态码")}</h2>
          </header>
          <BarList
            empty={t("暂无状态码记录")}
            rows={byStatus.map((item) => ({
              key: String(item.status_code),
              label: badge(String(item.status_code)),
              count: item.count,
              errors: item.errors,
            }))}
          />
        </section>
        {hasPromData && (
          <section className="panel">
            <header>
              <h2>{t("进程内累计")}</h2>
              <span className="muted">{t("自启动起")}</span>
            </header>
            <div className="bar-scroll">
              <h3 className="bar-group">{t("上游调用")}</h3>
              <BarList
                bare
                empty={t("暂无网关上游记录")}
                rows={upstreamByAccount.map((row) => ({
                  key: row.account,
                  label: accountLabels.get(row.account) || row.account,
                  title: [...row.ops.entries()]
                    .map(([op, n]) => `${op} ${n}`)
                    .join(" · "),
                  count: row.total,
                  errors: row.failed,
                }))}
              />
              <h3 className="bar-group">{t("媒体等待")}</h3>
              <BarList
                bare
                empty={t("暂无媒体状态机事件")}
                rows={mediaOutcomes.map((item) => ({
                  key: `${item.kind}-${item.outcome}`,
                  label: (
                    <>
                      <span className="bar-kind">{item.kind}</span>
                      <span title={item.outcome}>{badge(item.outcome)}</span>
                    </>
                  ),
                  count: item.count,
                }))}
              />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

type BarRow = {
  key: string;
  label: ReactNode;
  title?: string;
  count: number;
  errors?: number;
};

/** 紧凑条形列表：名称 + 数量 + 错误数，条长按最大值归一。 */
function BarList({
  rows,
  empty,
  bare = false,
}: {
  rows: BarRow[];
  empty: string;
  bare?: boolean;
}) {
  if (!rows.length)
    return bare ? <p className="bar-empty">{empty}</p> : <Empty text={empty} />;
  const max = Math.max(...rows.map((r) => r.count), 1);
  const list = (
    <ul className="bar-list">
      {rows.map((r, i) => (
        <li
          key={r.key}
          title={r.title}
          style={{ "--i": Math.min(i, 12) } as CSSProperties}
        >
          <div className="bar-line">
            <span className="bar-label">{r.label}</span>
            <span className="bar-count">
              {r.count}
              {r.errors ? (
                <em className="bar-errors">{t("{n} 错", { n: r.errors })}</em>
              ) : null}
            </span>
          </div>
          <div className="bar-track" aria-hidden="true">
            <i style={{ width: `${(r.count / max) * 100}%` }} />
            {r.errors ? (
              <b style={{ width: `${(r.errors / max) * 100}%` }} />
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
  return bare ? list : <div className="bar-scroll">{list}</div>;
}
