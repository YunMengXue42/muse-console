import { useState } from "react";
import { Api } from "../api";
import type { Key, Setter, Ask, ModalKind } from "../types";
import { Empty } from "../ui";
import { badge } from "../status";
import "../console-pages.css";

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
  const baseUrl = `${window.location.origin}/v1`;
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">(
    "idle",
  );
  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(baseUrl);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }
  return (
    <div className="console-page keys-page">
      <section
        className="panel api-endpoint"
        aria-labelledby="api-endpoint-title"
      >
        <div className="api-endpoint-copy">
          <h2 id="api-endpoint-title">调用地址</h2>
          <span className="muted">Base URL</span>
        </div>
        <div className="api-endpoint-field">
          <input
            aria-label="调用地址"
            value={baseUrl}
            readOnly
            spellCheck={false}
            onFocus={(event) => event.currentTarget.select()}
          />
          <button className="console-action" onClick={copyAddress}>
            {copyState === "copied" ? "已复制" : "复制地址"}
          </button>
        </div>
        <span className="api-endpoint-feedback" role="status">
          {copyState === "failed" ? "复制失败，请选中地址复制" : ""}
        </span>
      </section>
      <section className="panel">
        <header>
          <h2>应用密钥</h2>
          <span className="muted console-key-hint">明文仅在创建时显示一次</span>
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
                    <th
                      key={t}
                      className={
                        ["总调用", "24h 请求 / 错误"].includes(t)
                          ? "console-number"
                          : undefined
                      }
                    >
                      {t}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td data-label="名称 / 前缀">
                      <div className="console-identity">
                        <strong>{k.name || "未命名密钥"}</strong>
                        <small className="mono">{k.prefix}…</small>
                      </div>
                    </td>
                    <td data-label="状态">
                      {badge(k.revoked ? "disabled" : "active")}
                    </td>
                    <td data-label="总调用" className="console-number">
                      {k.usage.total}
                    </td>
                    <td data-label="24h 请求 / 错误" className="console-number">
                      {k.usage.requests_24h} / {k.usage.errors_24h}
                    </td>
                    <td data-label="操作">
                      <div className="row-actions">
                        <button
                          className="console-action"
                          onClick={() => {
                            setKeyEdit(k);
                            setLabel(k.name);
                          }}
                        >
                          重命名
                        </button>
                        <button
                          className={
                            k.revoked ? "console-action" : "console-caution"
                          }
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
          <Empty text="暂无应用密钥">
            <button className="primary" onClick={() => openModal("key")}>
              创建首个密钥
            </button>
          </Empty>
        )}
      </section>
      <section className="panel spaced">
        <header>
          <h2>内置身份</h2>
          <span className="muted">由服务配置管理，此处不可修改</span>
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
    </div>
  );
}
