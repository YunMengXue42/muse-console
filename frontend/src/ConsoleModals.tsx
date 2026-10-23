import { t } from "./i18n";
import type { Act, Detail, Key, ModalKind, Pool, Setter } from "./types";
import type { Account, Api, ImportResult, LoginSession } from "./api";
import { badge, statusNames } from "./status";
import { Modal } from "./ui";
import { LoginBrowser } from "./LoginBrowser";
import { CredentialDevices } from "./CredentialDevices";
import { extractMediaPreview, printable } from "./console-utils";

export function ConsoleModals({
  api,
  busy,
  act,
  notify,
  copy,
  onRefresh,
  accounts,
  pool,
  devicesOpen,
  setDevicesOpen,
  modal,
  setModal,
  edit,
  setEdit,
  maxConcurrency,
  setMaxConcurrency,
  mergeSource,
  setMergeSource,
  mergeTarget,
  setMergeTarget,
  keyEdit,
  setKeyEdit,
  label,
  setLabel,
  cookies,
  setCookies,
  note,
  setNote,
  content,
  setContent,
  preview,
  setPreview,
  imported,
  setImported,
  secret,
  setSecret,
  session,
  setSession,
  detail,
  setDetail,
  confirm,
  setConfirm,
}: {
  api: Api;
  busy: boolean;
  act: Act;
  notify: (message: string) => void;
  copy: (value: string) => Promise<void>;
  onRefresh: () => void;
  accounts: Account[];
  pool: Pool | undefined;
  devicesOpen: boolean;
  setDevicesOpen: Setter<boolean>;
  modal: ModalKind;
  setModal: Setter<ModalKind>;
  edit: Account | null;
  setEdit: Setter<Account | null>;
  maxConcurrency: string;
  setMaxConcurrency: Setter<string>;
  mergeSource: Account | null;
  setMergeSource: Setter<Account | null>;
  mergeTarget: string;
  setMergeTarget: Setter<string>;
  keyEdit: Key | null;
  setKeyEdit: Setter<Key | null>;
  label: string;
  setLabel: Setter<string>;
  cookies: string;
  setCookies: Setter<string>;
  note: string;
  setNote: Setter<string>;
  content: string;
  setContent: Setter<string>;
  preview: ImportResult | null;
  setPreview: Setter<ImportResult | null>;
  imported: boolean;
  setImported: Setter<boolean>;
  secret: string;
  setSecret: Setter<string>;
  session: LoginSession | null;
  setSession: Setter<LoginSession | null>;
  detail: Detail | null;
  setDetail: Setter<Detail | null>;
  confirm: { text: string; run: () => Promise<void> } | null;
  setConfirm: Setter<{ text: string; run: () => Promise<void> } | null>;
}) {
  return (
    <>
      {devicesOpen && (
        <CredentialDevices
          api={api}
          accounts={accounts}
          onClose={() => setDevicesOpen(false)}
        />
      )}
      {modal === "login" && (
        <Modal title={t("连接上游账号")} onClose={() => setModal(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const value = await api.request<LoginSession>(
                  "/login-sessions",
                  "POST",
                  { label },
                );
                setSession(value);
                setModal(null);
              });
            }}
          >
            <p className="muted">
              {t(
                "在 muse.ai 网页逐号登录，手动完成验证码；驱动不支持时会报错。",
              )}
            </p>
            <label>
              {t("账号备注")}
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder={t("例如：团队主账号")}
                maxLength={200}
              />
            </label>
            <footer>
              <button type="button" onClick={() => setModal(null)}>
                {t("取消")}
              </button>
              <button className="primary" disabled={busy}>
                {t("打开网页登录")}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {session && (
        <LoginBrowser
          api={api}
          session={session}
          notify={notify}
          onClose={() => setSession(null)}
          onComplete={() => {
            setSession(null);
            onRefresh();
          }}
        />
      )}
      {modal === "import" && (
        <Modal
          title={t("批量导入 Cookie")}
          wide
          onClose={() => {
            setModal(null);
            setContent("");
            setPreview(null);
          }}
        >
          <p className="muted">
            {t("支持 JSON 或文本；预览确认后入池。Cookie 不在浏览器持久保存。")}
          </p>
          <label>
            {t("选择文件")}
            <input
              type="file"
              accept=".json,.txt,application/json,text/plain"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file)
                  void file
                    .text()
                    .then((value) => {
                      setContent(value);
                      setPreview(null);
                      setImported(false);
                    })
                    .catch(() => notify(t("文件读取失败")));
              }}
            />
          </label>
          <label>
            {t("Cookie 内容")}
            <textarea
              rows={7}
              value={content}
              spellCheck={false}
              autoComplete="off"
              onChange={(e) => {
                setContent(e.target.value);
                setPreview(null);
                setImported(false);
              }}
              placeholder={t("粘贴 Cookie JSON 或文本")}
            />
          </label>
          {preview && (
            <>
              <div className="import-summary">
                {Object.entries(preview.summary).map(([name, value]) => (
                  <span className="badge" key={name}>
                    {t(statusNames[name] ?? name)} {value}
                  </span>
                ))}
              </div>
              <div className="import-results">
                {preview.data.map((row, i) => (
                  <div key={`${row.index}-${i}`}>
                    <span>
                      #{row.index} {row.label}
                    </span>
                    {badge(row.status)}
                    {row.message && <small>{row.message}</small>}
                  </div>
                ))}
              </div>
            </>
          )}
          <footer>
            <button
              onClick={() => {
                setModal(null);
                setContent("");
              }}
            >
              {t("关闭")}
            </button>
            <button
              disabled={busy || !content.trim()}
              onClick={() =>
                void act(async () => {
                  setPreview(
                    await api.request<ImportResult>(
                      "/accounts/import",
                      "POST",
                      { content, preview: true },
                    ),
                  );
                  setImported(false);
                })
              }
            >
              {t("预览检查")}
            </button>
            <button
              className="primary"
              disabled={busy || !preview || !preview.summary.ready || imported}
              onClick={() =>
                void act(async () => {
                  setPreview(
                    await api.request<ImportResult>(
                      "/accounts/import",
                      "POST",
                      { content, preview: false },
                    ),
                  );
                  setImported(true);
                  setContent("");
                }, t("导入已完成，请检查逐条结果"))
              }
            >
              {t("确认入池")}
            </button>
          </footer>
        </Modal>
      )}
      {edit && (
        <Modal
          title={t("编辑账号")}
          onClose={() => {
            setEdit(null);
            setCookies("");
            setMaxConcurrency("");
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                let parsed: unknown;
                try {
                  parsed = cookies.trim() ? JSON.parse(cookies) : undefined;
                } catch {
                  throw new Error(t("Cookie 必须是 JSON 对象。"));
                }
                if (
                  parsed &&
                  (typeof parsed !== "object" ||
                    Array.isArray(parsed) ||
                    Object.values(parsed).some((v) => typeof v !== "string"))
                )
                  throw new Error(t("Cookie 必须是名称到字符串的 JSON 对象。"));
                const trimmedMc = maxConcurrency.trim();
                let nextMc: number | null | undefined;
                if (trimmedMc) {
                  const num = Number(trimmedMc);
                  if (!Number.isInteger(num) || num < 1 || num > 16)
                    throw new Error(t("单账号并发上限必须是 1 到 16 的整数。"));
                  if (num !== (edit.max_concurrency ?? null)) nextMc = num;
                } else if (edit.max_concurrency != null) {
                  nextMc = null;
                }
                await api.request(`/accounts/${edit.id}`, "PATCH", {
                  label,
                  ...(parsed ? { cookies: parsed } : {}),
                  ...(nextMc !== undefined ? { max_concurrency: nextMc } : {}),
                });
                setEdit(null);
                setCookies("");
                setMaxConcurrency("");
              }, t("账号已更新"));
            }}
          >
            <label>
              {t("账号备注")}
              <input value={label} onChange={(e) => setLabel(e.target.value)} />
            </label>
            <label>
              {t("单账号并发上限（可选）")}
              <input
                type="number"
                min={1}
                max={16}
                value={maxConcurrency}
                onChange={(e) => setMaxConcurrency(e.target.value)}
                placeholder={t("留空使用全局默认（{n}）", {
                  n: pool?.max_concurrency ?? 1,
                })}
              />
            </label>
            <label>
              {t("更新 Cookie（可选）")}
              <textarea
                rows={5}
                value={cookies}
                onChange={(e) => setCookies(e.target.value)}
                placeholder={t('留空保留现有 Cookie；填写 {"name":"value"}')}
              />
            </label>
            <p className="fine">{t("现有 Cookie 不回传到此输入框。")}</p>
            <footer>
              <button
                type="button"
                onClick={() => {
                  setEdit(null);
                  setCookies("");
                  setMaxConcurrency("");
                }}
              >
                {t("取消")}
              </button>
              <button className="primary" disabled={busy}>
                {t("保存修改")}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {mergeSource && (
        <Modal
          title={t("合并重复账号")}
          onClose={() => {
            setMergeSource(null);
            setMergeTarget("");
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!mergeTarget) return;
              void act(async () => {
                await api.request(`/accounts/${mergeSource.id}/merge`, "POST", {
                  into: mergeTarget,
                });
                setMergeSource(null);
                setMergeTarget("");
              }, t("账号已合并"));
            }}
          >
            <p className="muted">
              {t(
                "将源账号「{name}」（{id}）合并进目标账号，累计计数与扩展设备绑定将迁移至目标账号，源账号随后移除。",
                {
                  name: mergeSource.label || mergeSource.id,
                  id: mergeSource.id,
                },
              )}
            </p>
            <label>
              {t("目标账号")}
              <select
                required
                value={mergeTarget}
                onChange={(e) => setMergeTarget(e.target.value)}
              >
                <option value="">{t("选择保留的目标账号")}</option>
                {accounts
                  .filter((a) => a.id !== mergeSource.id)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label ? `${a.label} (${a.id})` : a.id}
                    </option>
                  ))}
              </select>
            </label>
            <footer>
              <button
                type="button"
                onClick={() => {
                  setMergeSource(null);
                  setMergeTarget("");
                }}
              >
                {t("取消")}
              </button>
              <button className="primary" disabled={busy || !mergeTarget}>
                {t("确认合并")}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {modal === "key" && (
        <Modal title={t("创建 API 密钥")} onClose={() => setModal(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const result = await api.request<{ api_key: string }>(
                  "/keys",
                  "POST",
                  { name: label.trim(), note },
                );
                setSecret(result.api_key);
                setModal(null);
              }, t("密钥已创建"));
            }}
          >
            <label>
              {t("密钥名称")}
              <input
                required
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder={t("例如：生产应用")}
              />
            </label>
            <label>
              {t("备注")}
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
              />
            </label>
            <footer>
              <button type="button" onClick={() => setModal(null)}>
                {t("取消")}
              </button>
              <button className="primary" disabled={busy || !label.trim()}>
                {t("创建密钥")}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {keyEdit && (
        <Modal title={t("重命名密钥")} onClose={() => setKeyEdit(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await api.request(`/keys/${keyEdit.id}`, "PATCH", {
                  name: label.trim(),
                  ...(note !== (keyEdit.note ?? "") ? { note } : {}),
                });
                setKeyEdit(null);
              }, t("密钥信息已更新"));
            }}
          >
            <label>
              {t("名称")}
              <input
                required
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            <label>
              {t("备注")}
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
              />
            </label>
            <footer>
              <button type="button" onClick={() => setKeyEdit(null)}>
                {t("取消")}
              </button>
              <button className="primary" disabled={busy || !label.trim()}>
                {t("保存")}
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {secret && (
        <Modal title={t("保存你的新密钥")} onClose={() => setSecret("")}>
          <p className="muted">
            {t("此密钥只显示一次。关闭后无法再次查看，请保存在安全位置。")}
          </p>
          <div className="secret mono">{secret}</div>
          <footer>
            <button onClick={() => void copy(secret)}>{t("复制密钥")}</button>
            <button className="primary" onClick={() => setSecret("")}>
              {t("已安全保存")}
            </button>
          </footer>
        </Modal>
      )}
      {confirm && (
        <Modal
          title={t("确认操作")}
          onClose={() => {
            if (!busy) setConfirm(null);
          }}
        >
          <p>{confirm.text}</p>
          <footer>
            <button disabled={busy} onClick={() => setConfirm(null)}>
              {t("取消")}
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void act(confirm.run, t("操作已完成"))}
            >
              {t("确认")}
            </button>
          </footer>
        </Modal>
      )}
      {detail && (
        <Modal title={detail.title} wide onClose={() => setDetail(null)}>
          {(() => {
            const mediaItems = extractMediaPreview(detail.body);
            return mediaItems.length ? (
              <div className="detail-media-grid">
                {mediaItems.map((item, idx) => (
                  <figure
                    className="detail-media-card"
                    key={`${item.url}-${idx}`}
                  >
                    {item.kind === "video" ? (
                      <video
                        controls
                        preload="metadata"
                        src={item.url}
                        className="detail-media-player"
                      />
                    ) : (
                      <img
                        src={item.url}
                        alt={item.caption || t("生成图像预览")}
                        className="detail-media-image"
                      />
                    )}
                    <figcaption className="detail-media-meta">
                      {item.caption && (
                        <span className="muted">{item.caption}</span>
                      )}
                      <a
                        href={item.url}
                        download
                        target="_blank"
                        rel="noreferrer"
                      >
                        {t("下载 / 新窗口打开")}
                      </a>
                    </figcaption>
                  </figure>
                ))}
              </div>
            ) : null;
          })()}
          <pre className="detail">{printable(detail.body) ?? t("无结果")}</pre>
          <footer>
            <button
              type="button"
              onClick={() => void copy(printable(detail.body) ?? "")}
            >
              {t("复制 JSON")}
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => setDetail(null)}
            >
              {t("关闭")}
            </button>
          </footer>
        </Modal>
      )}
    </>
  );
}
