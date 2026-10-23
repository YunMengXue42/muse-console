import { t } from "../i18n";
import { time, type Account } from "../api";
import type { RequestRow, Key, Setter, Detail } from "../types";
import { Empty } from "../ui";
import { badge, statusNames } from "../status";
import "../console-pages.css";

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function RequestsPage({
  requests,
  total,
  filter,
  setFilter,
  accountFilter,
  setAccountFilter,
  pathFilter = "",
  setPathFilter,
  keyFilter = "",
  setKeyFilter,
  accounts = [],
  keys = [],
  hidePolls,
  setHidePolls,
  offset,
  setOffset,
  loading,
  setDetail,
}: {
  requests: RequestRow[];
  total: number;
  filter: string;
  setFilter: Setter<string>;
  accountFilter: string;
  setAccountFilter: Setter<string>;
  pathFilter?: string;
  setPathFilter?: Setter<string>;
  keyFilter?: string;
  setKeyFilter?: Setter<string>;
  accounts?: Account[];
  keys?: Key[];
  hidePolls: boolean;
  setHidePolls: Setter<boolean>;
  offset: number;
  setOffset: Setter<number>;
  loading: boolean;
  setDetail: Setter<Detail | null>;
}) {
  const hasActiveFilter = Boolean(
    filter || accountFilter || pathFilter || keyFilter,
  );
  return (
    <section className="console-page requests-page panel">
      <header>
        <h2>{t("请求记录")}</h2>
        <span className="badge">{t("{n} 条", { n: total })}</span>
      </header>
      <div className="table-tools">
        <label>
          {t("响应状态")}
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">{t("全部状态")}</option>
            {["failed", "2xx", "3xx", "4xx", "5xx"].map((s) => (
              <option key={s} value={s}>
                {s === "failed" ? t("全部失败") : s}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("账号 ID")}
          <input
            list="request-account-options"
            value={accountFilter}
            placeholder={t("按账号 ID 筛选")}
            onChange={(e) => {
              setAccountFilter(e.target.value);
              setOffset(0);
            }}
          />
          <datalist id="request-account-options">
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label || a.id}
              </option>
            ))}
          </datalist>
        </label>
        {setPathFilter && (
          <label>
            {t("接口路径")}
            <select
              value={pathFilter}
              onChange={(e) => {
                setPathFilter(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">{t("全部接口")}</option>
              <option value="/v1/chat/completions">Chat (/v1/chat)</option>
              <option value="/v1/responses">Responses (/v1/responses)</option>
              <option value="/v1/images">{t("图像 (/v1/images)")}</option>
              <option value="/v1/videos">{t("视频 (/v1/videos)")}</option>
            </select>
          </label>
        )}
        {setKeyFilter && (
          <label>
            {t("应用密钥")}
            <input
              list="request-key-options"
              value={keyFilter}
              placeholder={t("按密钥 ID 筛选")}
              onChange={(e) => {
                setKeyFilter(e.target.value);
                setOffset(0);
              }}
            />
            <datalist id="request-key-options">
              <option value="admin">{t("admin（内置管理员）")}</option>
              <option value="legacy">{t("legacy（内置默认密钥）")}</option>
              {keys.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name || k.id}
                </option>
              ))}
            </datalist>
          </label>
        )}
        <label className="check">
          <input
            type="checkbox"
            checked={hidePolls}
            onChange={(e) => {
              setHidePolls(e.target.checked);
              setOffset(0);
            }}
          />
          {t("隐藏任务轮询")}
        </label>
        {hasActiveFilter && (
          <button
            type="button"
            className="console-action"
            onClick={() => {
              setFilter("");
              setAccountFilter("");
              setPathFilter?.("");
              setKeyFilter?.("");
              setOffset(0);
            }}
          >
            {t("清除筛选")}
          </button>
        )}
      </div>
      {requests.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {["时间", "请求", "模型 / 账号", "状态", "耗时", "详情"].map(
                  (label) => (
                    <th key={label}>{t(label)}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {requests.map((r) => (
                <tr key={r.id}>
                  <td data-label={t("时间")}>{time(r.ts)}</td>
                  <td data-label={t("请求")}>
                    <div className="console-identity">
                      <div>
                        <strong>{r.method}</strong>
                        {r.stream ? (
                          <span className="badge stream-tag">SSE</span>
                        ) : null}
                      </div>
                      <small className="mono">{r.path}</small>
                      {r.error && (
                        <small className="danger-text">{r.error}</small>
                      )}
                    </div>
                  </td>
                  <td data-label={t("模型 / 账号")}>
                    <div className="console-identity">
                      {r.model ?? "—"}
                      <small className="mono">
                        {r.account_id ?? "—"}
                        {r.key_name ? ` · ${r.key_name}` : ""}
                      </small>
                    </div>
                  </td>
                  <td data-label={t("状态")}>
                    <div className="console-identity">
                      {badge(
                        r.task_status === "failed"
                          ? "failed"
                          : String(r.status_code),
                      )}
                      {r.task_status && r.task_status !== "failed" && (
                        <small className="muted">
                          {t("任务 {status}", {
                            status: t(
                              statusNames[r.task_status] ?? r.task_status,
                            ),
                          })}
                        </small>
                      )}
                    </div>
                  </td>
                  <td data-label={t("耗时")} className="mono">
                    <div className="console-identity">
                      <span>{r.latency_ms} ms</span>
                      {r.task_ms != null && (
                        <small className="mono">
                          {t("任务 {n}s", { n: (r.task_ms / 1000).toFixed(1) })}
                        </small>
                      )}
                    </div>
                  </td>
                  <td data-label={t("详情")}>
                    <button
                      className="console-action"
                      onClick={() =>
                        setDetail({
                          title: t("请求 #{n}", { n: r.id }),
                          body: r,
                        })
                      }
                    >
                      {t("查看")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty text={t("暂无匹配请求")} />
      )}
      <div className="pagination">
        <span>
          {total
            ? `${offset + 1}–${Math.min(offset + 50, total)} / ${total}`
            : t("0 条记录")}
        </span>
        <button
          disabled={offset === 0 || loading}
          onClick={() => setOffset((v) => Math.max(0, v - 50))}
        >
          {t("上一页")}
        </button>
        <button
          disabled={offset + 50 >= total || loading}
          onClick={() => setOffset((v) => v + 50)}
        >
          {t("下一页")}
        </button>
      </div>
    </section>
  );
}
