import { t } from "../i18n";
import { useState } from "react";
import { Api, time } from "../api";
import type { Task, Setter, Detail, Ask } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";
import "../console-pages.css";

function taskErrorText(error: unknown): string | null {
  if (!error) return null;
  if (typeof error === "string") return error;
  if (typeof error === "object") {
    const obj = error as { code?: unknown; message?: unknown };
    const code = typeof obj.code === "string" ? obj.code : "";
    const msg = typeof obj.message === "string" ? obj.message : "";
    if (code && msg) return `${code}: ${msg}`;
    if (msg || code) return msg || code;
  }
  return null;
}

function formatElapsed(createdAt: number, updatedAt?: number): string | null {
  if (!updatedAt || updatedAt < createdAt) return null;
  const sec = Math.max(0, Math.round(updatedAt - createdAt));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? `${min}m ${rem}s` : `${min}m`;
}

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function TasksPage({
  tasks,
  busy,
  setDetail,
  ask,
  api,
}: {
  tasks: Task[];
  busy: boolean;
  setDetail: Setter<Detail | null>;
  ask: Ask;
  api: Api;
}) {
  const [statusFilter, setStatusFilter] = useState("all");
  const isRunning = (s: string) =>
    ["pending", "queued", "running", "processing"].includes(s);
  const counts = {
    all: tasks.length,
    active: tasks.filter((t) => isRunning(t.status)).length,
    succeeded: tasks.filter((t) =>
      ["succeeded", "completed"].includes(t.status),
    ).length,
    failed: tasks.filter((t) => ["failed", "cancelled"].includes(t.status))
      .length,
  };
  const visibleTasks = tasks.filter((t) => {
    if (statusFilter === "active") return isRunning(t.status);
    if (statusFilter === "succeeded")
      return ["succeeded", "completed"].includes(t.status);
    if (statusFilter === "failed")
      return ["failed", "cancelled"].includes(t.status);
    return true;
  });

  return (
    <section className="console-page tasks-page panel">
      <header>
        <h2>{t("最近任务")}</h2>
        <span className="muted">{t("自动同步 · 最多 100 条")}</span>
      </header>
      {tasks.length > 0 && (
        <div
          className="pool-filters"
          role="group"
          aria-label={t("任务状态筛选")}
        >
          {[
            { value: "all", label: "全部", count: counts.all },
            { value: "active", label: "进行中", count: counts.active },
            { value: "succeeded", label: "已完成", count: counts.succeeded },
            { value: "failed", label: "失败 / 取消", count: counts.failed },
          ].map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={statusFilter === item.value}
              onClick={() => setStatusFilter(item.value)}
            >
              {t(item.label)} <span>{item.count}</span>
            </button>
          ))}
        </div>
      )}
      {tasks.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {[
                  "任务 ID",
                  "类型 / 规格",
                  "状态 / 进度",
                  "时间 / 耗时",
                  "操作",
                ].map((label) => (
                  <th key={label}>{t(label)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleTasks.map((task) => {
                const prompt = task.request?.prompt;
                const errSummary = taskErrorText(task.error);
                const elapsed = formatElapsed(task.created_at, task.updated_at);
                const dur = task.request?.duration ?? task.request?.seconds;
                const specs = [
                  task.request?.size,
                  dur ? `${dur}s` : null,
                  task.request?.n && task.request.n > 1
                    ? `×${task.request.n}`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <tr key={task.id}>
                    <td data-label={t("任务 ID")}>
                      <div className="console-identity">
                        <strong className="mono">{task.id}</strong>
                        {prompt && (
                          <small className="task-prompt" title={prompt}>
                            {prompt}
                          </small>
                        )}
                        {errSummary && (
                          <small className="danger-text">{errSummary}</small>
                        )}
                      </div>
                    </td>
                    <td data-label={t("类型 / 规格")}>
                      <div className="console-identity">
                        <span>{task.kind}</span>
                        {specs && <small className="mono">{specs}</small>}
                      </div>
                    </td>
                    <td data-label={t("状态 / 进度")}>
                      <div className="task-status-cell">
                        {badge(task.status)}
                        {typeof task.progress === "number" &&
                          isRunning(task.status) && (
                            <span className="mono muted task-progress-text">
                              {task.progress}%
                            </span>
                          )}
                      </div>
                    </td>
                    <td data-label={t("时间 / 耗时")}>
                      <div className="console-identity">
                        <span>{time(task.created_at)}</span>
                        {elapsed && (
                          <small className="mono">
                            {t("耗时 {elapsed}", { elapsed })}
                          </small>
                        )}
                      </div>
                    </td>
                    <td data-label={t("操作")}>
                      <div className="row-actions">
                        <button
                          className="console-action"
                          onClick={() =>
                            setDetail({
                              title: t("任务 {n}", { n: task.id }),
                              body: {
                                ...(task.request
                                  ? { request: task.request }
                                  : {}),
                                result: task.result,
                                error: task.error,
                              },
                            })
                          }
                        >
                          {t("结果详情")}
                        </button>
                        {isRunning(task.status) && (
                          <button
                            className="console-caution"
                            disabled={busy}
                            onClick={() =>
                              ask(
                                t("取消任务 {n}？", { n: task.id }),
                                async () => {
                                  const result = await api.request<{
                                    cancelled: boolean;
                                  }>(`/tasks/${task.id}/cancel`, "POST");
                                  if (!result.cancelled)
                                    throw new Error(
                                      t("任务未取消，可能已结束，请刷新查看。"),
                                    );
                                },
                              )
                            }
                          >
                            {t("取消任务")}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!visibleTasks.length && (
            <Empty text={t("当前筛选下无任务")}>
              <button type="button" onClick={() => setStatusFilter("all")}>
                {t("查看全部任务")}
              </button>
            </Empty>
          )}
        </div>
      ) : (
        <Empty text={t("暂无任务")} />
      )}
    </section>
  );
}
