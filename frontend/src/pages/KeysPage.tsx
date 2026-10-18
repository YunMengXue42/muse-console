import { Api } from "../api";
import type { Key, Setter, Ask, ModalKind } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";

/** 页面只渲染快照并转交操作；轮询由 App 统一负责。 */
export function KeysPage({
  keys,
  builtin,
  setKeyEdit,
  setLabel,
  ask,
  api,
  openModal,
}: {
  keys: Key[];
  builtin: Key[];
  setKeyEdit: Setter<Key | null>;
  setLabel: Setter<string>;
  ask: Ask;
  api: Api;
  openModal: (kind: ModalKind) => void;
}) {
  return (
    <>
      <section className="panel">
        <header>
          <h2>应用密钥</h2>
          <span className="muted">明文仅在创建时显示一次</span>
        </header>
        {keys.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {[
                    "名称 / 前缀",
                    "状态",
                    "总调用",
                    "24h 请求 / 错误",
                    "操作",
                  ].map((t) => (
                    <th key={t}>{t}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td data-label="名称 / 前缀">
                      <strong>{k.name || "未命名密钥"}</strong>
                      <small className="mono">{k.prefix}…</small>
                    </td>
                    <td data-label="状态">
                      {badge(k.revoked ? "disabled" : "active")}
                    </td>
                    <td data-label="总调用">{k.usage.total}</td>
                    <td data-label="24h 请求 / 错误" className="mono">
                      {k.usage.requests_24h} / {k.usage.errors_24h}
                    </td>
                    <td data-label="操作">
                      <div className="row-actions">
                        <button
                          onClick={() => {
                            setKeyEdit(k);
                            setLabel(k.name);
                          }}
                        >
                          重命名
                        </button>
                        <button
                          onClick={() =>
                            ask(
                              `${k.revoked ? "恢复" : "吊销"}密钥「${k.name}」？${k.revoked ? "" : "现有应用将立即失去访问权限。"}`,
                              () =>
                                api.request(`/keys/${k.id}`, "PATCH", {
                                  revoked: !k.revoked,
                                }),
                            )
                          }
                        >
                          {k.revoked ? "恢复" : "吊销"}
                        </button>
                        <button
                          className="danger-text"
                          onClick={() =>
                            ask(
                              `永久删除密钥「${k.name}」？此操作不可撤销。`,
                              () => api.request(`/keys/${k.id}`, "DELETE"),
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
          </div>
        ) : (
          <Empty text="尚未创建应用密钥">
            <button className="primary" onClick={() => openModal("key")}>
              创建首个密钥
            </button>
          </Empty>
        )}
      </section>
      <section className="panel spaced">
        <header>
          <h2>内置身份</h2>
          <span className="muted">由服务配置管理，不可在此修改</span>
        </header>
        {builtin.length ? (
          <div className="builtin">
            {builtin.map((k) => (
              <div key={k.id}>
                <strong>{k.name}</strong>
                <span>{k.usage.total} 次总调用</span>
                <small>
                  24h 请求 {k.usage.requests_24h} / 错误 {k.usage.errors_24h}
                </small>
              </div>
            ))}
          </div>
        ) : (
          <Empty text="无内置身份记录" />
        )}
      </section>
    </>
  );
}
