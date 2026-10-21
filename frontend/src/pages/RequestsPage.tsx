import { time } from "../api";
import type { RequestRow, Setter, Detail } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";
import "../console-pages.css";

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function RequestsPage({
  requests,
  total,
  filter,
  setFilter,
  accountFilter,
  setAccountFilter,
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
  hidePolls: boolean;
  setHidePolls: Setter<boolean>;
  offset: number;
  setOffset: Setter<number>;
  loading: boolean;
  setDetail: Setter<Detail | null>;
}) {
  return (
    <section className="console-page requests-page panel">
      <header>
        <h2>请求记录</h2>
        <span className="badge">{total} 条</span>
      </header>
      <div className="table-tools">
        <label>
          响应状态
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部状态</option>
            {["failed", "2xx", "3xx", "4xx", "5xx"].map((s) => (
              <option key={s} value={s}>
                {s === "failed" ? "全部失败" : s}
              </option>
            ))}
          </select>
        </label>
        <label>
          账号 ID
          <input
            value={accountFilter}
            placeholder="按账号 ID 筛选"
            onChange={(e) => {
              setAccountFilter(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={hidePolls}
            onChange={(e) => {
              setHidePolls(e.target.checked);
              setOffset(0);
            }}
          />
          隐藏任务轮询
        </label>
      </div>
      {requests.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {["时间", "请求", "模型 / 账号", "状态", "耗时", "详情"].map(
                  (t) => (
                    <th key={t}>{t}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {requests.map((r) => (
                <tr key={r.id}>
                  <td data-label="时间">{time(r.ts)}</td>
                  <td data-label="请求">
                    <div className="console-identity">
                      <strong>{r.method}</strong>
                      <small className="mono">{r.path}</small>
                    </div>
                  </td>
                  <td data-label="模型 / 账号">
                    <div className="console-identity">
                      {r.model ?? "—"}
                      <small className="mono">{r.account_id ?? "—"}</small>
                    </div>
                  </td>
                  <td data-label="状态">
                    {badge(
                      r.task_status === "failed"
                        ? "failed"
                        : String(r.status_code),
                    )}
                  </td>
                  <td data-label="耗时" className="mono">
                    {r.latency_ms} ms
                  </td>
                  <td data-label="详情">
                    <button
                      className="console-action"
                      onClick={() =>
                        setDetail({ title: `请求 #${r.id}`, body: r })
                      }
                    >
                      查看
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty text="暂无匹配请求" />
      )}
      <div className="pagination">
        <span>
          {total
            ? `${offset + 1}–${Math.min(offset + 50, total)} / ${total}`
            : "0 条记录"}
        </span>
        <button
          disabled={offset === 0 || loading}
          onClick={() => setOffset((v) => Math.max(0, v - 50))}
        >
          上一页
        </button>
        <button
          disabled={offset + 50 >= total || loading}
          onClick={() => setOffset((v) => v + 50)}
        >
          下一页
        </button>
      </div>
    </section>
  );
}
