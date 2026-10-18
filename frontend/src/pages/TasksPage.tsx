import { Api, time } from "../api";
import type { Task, Setter, Detail, Ask } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";

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
  return (
    <section className="panel">
      <header>
        <h2>最近任务</h2>
        <span className="muted">自动同步 · 最多 100 条</span>
      </header>
      {tasks.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {["任务 ID", "类型", "状态", "创建时间", "操作"].map((t) => (
                  <th key={t}>{t}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td data-label="任务 ID" className="mono">
                    {task.id}
                  </td>
                  <td data-label="类型">{task.kind}</td>
                  <td data-label="状态">{badge(task.status)}</td>
                  <td data-label="创建时间">{time(task.created_at)}</td>
                  <td data-label="操作">
                    <div className="row-actions">
                      <button
                        onClick={() =>
                          setDetail({
                            title: `任务 ${task.id}`,
                            body: {
                              result: task.result,
                              error: task.error,
                            },
                          })
                        }
                      >
                        结果详情
                      </button>
                      {["pending", "queued", "running", "processing"].includes(
                        task.status,
                      ) && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            ask(`取消任务 ${task.id}？`, async () => {
                              const result = await api.request<{
                                cancelled: boolean;
                              }>(`/tasks/${task.id}/cancel`, "POST");
                              if (!result.cancelled)
                                throw new Error(
                                  "任务未取消，可能已结束，请刷新查看。",
                                );
                            })
                          }
                        >
                          取消任务
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty text="暂无任务" />
      )}
    </section>
  );
}
