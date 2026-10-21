import type { Key, Task, RequestRow, Status, Pool } from "./types";
import { PoolPage } from "./pages/PoolPage";
import { OverviewPage } from "./pages/OverviewPage";
import { TasksPage } from "./pages/TasksPage";
import { RequestsPage } from "./pages/RequestsPage";
import { KeysPage } from "./pages/KeysPage";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Api,
  ApiError,
  type Account,
  type Stats,
  type ImportResult,
  type LoginSession,
} from "./api";
import { Icon } from "./ui";
import { ConsoleModals } from "./ConsoleModals";
import {
  formatConsoleHash,
  parseConsoleHash,
  parsePrometheusMetrics,
  type ParsedMetrics,
} from "./console-utils";

const pages = ["账号池", "调用概览", "任务中心", "请求日志", "API 密钥"];
const descriptions = [
  "管理上游会话与请求调度。",
  "查看请求统计与服务状态。",
  "查看异步任务、结果与失败原因。",
  "按状态或账号查询请求。",
  "管理应用密钥与访问权限。",
];

/** 全部页面共用认证、操作反馈与顺序轮询；输入草稿独立于服务器快照。 */
export default function App() {
  const initialRoute = useMemo(() => {
    const saved = sessionStorage.getItem("muse.console.key") ?? "";
    return saved ? parseConsoleHash(window.location.hash) : { page: 0 };
  }, []);
  const [key, setKey] = useState(
      () => sessionStorage.getItem("muse.console.key") ?? "",
    ),
    [draft, setDraft] = useState(""),
    [loginError, setLoginError] = useState(""),
    [authenticating, setAuthenticating] = useState(false);
  const [theme, setTheme] = useState(
      () => localStorage.getItem("muse.console.theme") ?? "light",
    ),
    [page, setPage] = useState(initialRoute.page),
    [toast, setToast] = useState(""),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>(),
    [accounts, setAccounts] = useState<Account[]>([]),
    [pool, setPool] = useState<Pool>(),
    [stats, setStats] = useState<Stats>(),
    [promMetrics, setPromMetrics] = useState<ParsedMetrics>(),
    [tasks, setTasks] = useState<Task[]>([]),
    [requests, setRequests] = useState<RequestRow[]>([]),
    [total, setTotal] = useState(0),
    [keys, setKeys] = useState<Key[]>([]),
    [builtin, setBuiltin] = useState<Key[]>([]);
  const [windowValue, setWindowValue] = useState(
      initialRoute.windowValue ?? "24h",
    ),
    [filter, setFilter] = useState(initialRoute.filter ?? ""),
    [accountFilter, setAccountFilter] = useState(
      initialRoute.accountFilter ?? "",
    ),
    [pathFilter, setPathFilter] = useState(initialRoute.pathFilter ?? ""),
    [keyFilter, setKeyFilter] = useState(initialRoute.keyFilter ?? ""),
    [hidePolls, setHidePolls] = useState(initialRoute.hidePolls ?? true),
    [offset, setOffset] = useState(0),
    [search, setSearch] = useState(""),
    [refresh, setRefresh] = useState(0);
  const [devicesOpen, setDevicesOpen] = useState(false);
  const [modal, setModal] = useState<"import" | "login" | "key" | null>(null),
    [edit, setEdit] = useState<Account | null>(null),
    [maxConcurrency, setMaxConcurrency] = useState(""),
    [mergeSource, setMergeSource] = useState<Account | null>(null),
    [mergeTarget, setMergeTarget] = useState(""),
    [keyEdit, setKeyEdit] = useState<Key | null>(null),
    [label, setLabel] = useState(""),
    [cookies, setCookies] = useState(""),
    [note, setNote] = useState(""),
    [content, setContent] = useState(""),
    [preview, setPreview] = useState<ImportResult | null>(null),
    [imported, setImported] = useState(false),
    [secret, setSecret] = useState(""),
    [session, setSession] = useState<LoginSession | null>(null),
    [detail, setDetail] = useState<{ title: string; body: unknown } | null>(
      null,
    ),
    [confirm, setConfirm] = useState<{
      text: string;
      run: () => Promise<void>;
    } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined),
    generation = useRef(0);
  const notify = useCallback((message: string) => {
    setToast(message);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 5000);
  }, []);
  const logout = useCallback(() => {
    generation.current++;
    sessionStorage.removeItem("muse.console.key");
    if (window.location.hash) {
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
    }
    setPage(0);
    setWindowValue("24h");
    setFilter("");
    setAccountFilter("");
    setPathFilter("");
    setKeyFilter("");
    setHidePolls(true);
    setOffset(0);
    setSession(null);
    setDevicesOpen(false);
    setKey("");
    setDraft("");
    setSecret("");
    setContent("");
    setCookies("");
    setMaxConcurrency("");
    setMergeSource(null);
    setMergeTarget("");
    setPreview(null);
    setModal(null);
    setEdit(null);
    setKeyEdit(null);
    setDetail(null);
    setConfirm(null);
    setStatus(undefined);
    setAccounts([]);
    setStats(undefined);
    setPromMetrics(undefined);
    setRequests([]);
    setTasks([]);
    setKeys([]);
    setBuiltin([]);
    setError("");
  }, []);
  const api = useMemo(
    () =>
      new Api(key, () => {
        logout();
        setLoginError("会话已失效，请重新输入管理员 Key。");
      }),
    [key, logout],
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("muse.console.theme", theme);
  }, [theme]);
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  useEffect(() => {
    if (!key) {
      if (window.location.hash) {
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      }
      return;
    }
    const nextHash = formatConsoleHash({
      page,
      windowValue,
      filter,
      accountFilter,
      pathFilter,
      keyFilter,
      hidePolls,
    });
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, "", nextHash);
    }
  }, [
    key,
    page,
    windowValue,
    filter,
    accountFilter,
    pathFilter,
    keyFilter,
    hidePolls,
  ]);
  useEffect(() => {
    if (!key) return;
    function onHashChange() {
      const route = parseConsoleHash(window.location.hash);
      setPage(route.page);
      if (route.windowValue !== undefined) setWindowValue(route.windowValue);
      if (route.filter !== undefined) setFilter(route.filter);
      if (route.accountFilter !== undefined)
        setAccountFilter(route.accountFilter);
      if (route.pathFilter !== undefined) setPathFilter(route.pathFilter);
      if (route.keyFilter !== undefined) setKeyFilter(route.keyFilter);
      if (route.hidePolls !== undefined) setHidePolls(route.hidePolls);
      setOffset(0);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [key]);
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const revision = ++generation.current;
    setLoading(true);
    setError("");
    async function load() {
      clearTimeout(timer);
      try {
        const common = await api.request<Status>(
          "/status",
          "GET",
          undefined,
          controller.signal,
        );
        if (stopped || revision !== generation.current) return;
        setStatus(common);
        if (page === 0) {
          const [a, p] = await Promise.all([
            api.request<{ data: Account[] }>(
              "/accounts",
              "GET",
              undefined,
              controller.signal,
            ),
            api.request<Pool>("/pool", "GET", undefined, controller.signal),
          ]);
          if (stopped) return;
          setAccounts(a.data);
          setPool(p);
        }
        if (page === 1) {
          const [data, rawMetrics] = await Promise.all([
            api.request<Stats>(
              `/stats?window=${windowValue}`,
              "GET",
              undefined,
              controller.signal,
            ),
            api.requestText("/metrics", controller.signal).catch(() => ""),
          ]);
          if (!stopped) {
            setStats(data);
            setPromMetrics(
              rawMetrics.startsWith("# ")
                ? parsePrometheusMetrics(rawMetrics)
                : undefined,
            );
          }
        }
        if (page === 2) {
          const data = await api.request<{ data: Task[] }>(
            "/tasks",
            "GET",
            undefined,
            controller.signal,
          );
          if (!stopped) setTasks(data.data);
        }
        if (page === 3) {
          const query = new URLSearchParams({
            hide_polls: String(hidePolls),
            limit: "50",
            offset: String(offset),
          });
          if (filter) query.set("status", filter);
          if (accountFilter) query.set("account_id", accountFilter);
          if (pathFilter) query.set("path", pathFilter);
          if (keyFilter) query.set("key_id", keyFilter);
          const data = await api.request<{ data: RequestRow[]; total: number }>(
            `/requests?${query}`,
            "GET",
            undefined,
            controller.signal,
          );
          if (!stopped) {
            setRequests(data.data);
            setTotal(data.total);
          }
        }
        if (page === 4) {
          const data = await api.request<{ data: Key[]; builtin: Key[] }>(
            "/keys",
            "GET",
            undefined,
            controller.signal,
          );
          if (!stopped) {
            setKeys(data.data);
            setBuiltin(data.builtin);
          }
        }
        if (!stopped) setError("");
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      } finally {
        if (!stopped) {
          setLoading(false);
          if (document.visibilityState !== "hidden") {
            timer = setTimeout(() => void load(), 10000);
          }
        }
      }
    }
    function onVisibilityChange() {
      if (stopped) return;
      if (document.visibilityState === "hidden") {
        clearTimeout(timer);
      } else {
        void load();
      }
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    void load();
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      controller.abort();
      clearTimeout(timer);
    };
  }, [
    api,
    key,
    page,
    windowValue,
    filter,
    accountFilter,
    pathFilter,
    keyFilter,
    hidePolls,
    offset,
    refresh,
  ]);
  /** 变更使用统一错误通道；失败不关闭表单，避免丢失用户输入。 */
  async function act(action: () => Promise<void>, message?: string) {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      setError("");
      setRefresh((v) => v + 1);
      if (message) notify(message);
    } catch (e) {
      setError((e as Error).message);
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function authenticate() {
    setAuthenticating(true);
    setLoginError("");
    try {
      await new Api(draft.trim()).request("/status");
      sessionStorage.setItem("muse.console.key", draft.trim());
      setKey(draft.trim());
      setDraft("");
    } catch (e) {
      setLoginError(
        e instanceof ApiError && e.status === 401
          ? "管理员 Key 不正确。"
          : (e as Error).message,
      );
    } finally {
      setAuthenticating(false);
    }
  }
  function openModal(value: typeof modal) {
    setLabel("");
    setNote("");
    setContent("");
    setPreview(null);
    setImported(false);
    setModal(value);
  }
  function ask(text: string, action: () => Promise<unknown>) {
    setConfirm({
      text,
      run: async () => {
        await action();
        setConfirm(null);
      },
    });
  }
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      notify("已复制到剪贴板");
    } catch {
      notify("复制失败，请手动选中并复制。");
    }
  }
  const themeButton = (
    <button
      className="theme-toggle"
      onClick={() => setTheme(theme === "light" ? "dark" : "light")}
      aria-label="切换主题"
      title={theme === "light" ? "切换深色模式" : "切换浅色模式"}
    >
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {theme === "light" ? (
          <path d="M20.5 13a8.5 8.5 0 0 1-9.5-9.5A8.5 8.5 0 1 0 20.5 13Z" />
        ) : (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
          </>
        )}
      </svg>
      <span>{theme === "light" ? "深色模式" : "浅色模式"}</span>
    </button>
  );
  if (!key)
    return (
      <div className="login-page">
        <div className="login-theme">{themeButton}</div>
        <form
          className="login-card"
          onSubmit={(e) => {
            e.preventDefault();
            void authenticate();
          }}
        >
          <h1>Muse Console</h1>
          <label>
            <input
              aria-label="管理员 Key"
              placeholder="管理员 Key"
              required
              type="password"
              autoComplete="off"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          </label>
          {loginError && (
            <p className="error" role="alert">
              {loginError}
            </p>
          )}
          <button
            className="primary full"
            disabled={authenticating || !draft.trim()}
          >
            {authenticating ? "验证中…" : "进入控制台"}
          </button>
          <p className="fine login-caption">管理账号与 API 调用</p>
        </form>
      </div>
    );
  const metrics = status?.accounts;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/dashboard" aria-label="Muse Console 首页">
          <span className="brand-wordmark">
            Muse<span>Console</span>
          </span>
        </a>
        <nav aria-label="主导航">
          {pages.map((name, i) => (
            <button
              className={page === i ? "selected" : ""}
              key={name}
              aria-current={page === i ? "page" : undefined}
              onClick={() => {
                setPage(i);
                setError("");
              }}
            >
              <Icon index={i} />
              {name}
              {i === 0 && metrics && (
                <span className="nav-count">{metrics.total}</span>
              )}
              {i === 2 && status?.tasks && status.tasks.pending > 0 && (
                <span className="nav-count">{status.tasks.pending}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="side-bottom" aria-label="实例与偏好">
          <div className="instance-status" role="status">
            <span
              className={`dot ${status ? (status.driver.ok ? "online" : "") : "pending"}`}
              aria-hidden="true"
            />
            <div className="instance-copy">
              <span className="instance-state">
                {status
                  ? status.driver.ok
                    ? "驱动在线"
                    : "驱动异常"
                  : "连接中"}
              </span>
              <span className="instance-meta">
                <span>{status?.driver.driver ?? "等待服务响应"}</span>
                {status && (
                  <span className="instance-version">v{status.version}</span>
                )}
              </span>
            </div>
          </div>
          <div className="instance-actions">
            {themeButton}
            <button
              className="console-logout"
              onClick={logout}
              aria-label="退出控制台"
              title="退出控制台"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M10 4H5v16h5m4-12 4 4-4 4m-5-4h13" />
              </svg>
              <span>退出</span>
            </button>
          </div>
        </div>
      </aside>
      <main className={page === 0 ? "pool-workspace" : undefined}>
        {status?.driver.driver === "mock" && (
          <div className="workspace-context">
            <span className="environment-label">演示环境 · 模拟数据</span>
          </div>
        )}
        <header className="page-header">
          <div>
            <div className="eyebrow">
              {page === 0 ? "ACCOUNT MANAGEMENT" : "MUSE CONSOLE"}
            </div>
            <h1>{pages[page]}</h1>
            <p className="muted">{descriptions[page]}</p>
          </div>
          <div className="toolbar">
            <button onClick={() => setRefresh((v) => v + 1)} disabled={loading}>
              {loading ? "同步中…" : "刷新数据"}
            </button>
            {page === 0 && (
              <>
                <button onClick={() => setDevicesOpen(true)}>
                  扩展设备配对
                </button>
                <button onClick={() => openModal("import")}>
                  批量导入 Cookie
                </button>
                <button className="primary" onClick={() => openModal("login")}>
                  ＋ 网页登录
                </button>
              </>
            )}
            {page === 4 && (
              <button className="primary" onClick={() => openModal("key")}>
                ＋ 创建密钥
              </button>
            )}
          </div>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button onClick={() => setRefresh((v) => v + 1)}>重试</button>
          </div>
        )}
        {loading && (
          <div className="loading" role="status">
            正在同步数据…
          </div>
        )}
        {page === 0 && (
          <PoolPage
            metrics={metrics}
            accounts={accounts}
            pool={pool}
            search={search}
            setSearch={setSearch}
            busy={busy}
            act={act}
            api={api}
            setEdit={setEdit}
            setLabel={setLabel}
            setCookies={setCookies}
            setMaxConcurrency={setMaxConcurrency}
            setMergeSource={(acc) => {
              setMergeSource(acc);
              setMergeTarget("");
            }}
            capabilities={status?.capabilities}
            openModal={openModal}
            setPage={setPage}
            ask={ask}
          />
        )}
        {page === 1 && (
          <OverviewPage
            stats={stats}
            windowValue={windowValue}
            setWindowValue={setWindowValue}
            accounts={accounts}
            promMetrics={promMetrics}
            setDetail={setDetail}
            onFilterRequests={({ accountId, keyId, status: st }) => {
              setAccountFilter(accountId ?? "");
              setKeyFilter(keyId ?? "");
              setFilter(st ?? "");
              setPathFilter("");
              setOffset(0);
              setPage(3);
            }}
          />
        )}
        {page === 2 && (
          <TasksPage
            tasks={tasks}
            busy={busy}
            setDetail={setDetail}
            ask={ask}
            api={api}
          />
        )}
        {page === 3 && (
          <RequestsPage
            requests={requests}
            total={total}
            filter={filter}
            setFilter={setFilter}
            accountFilter={accountFilter}
            setAccountFilter={setAccountFilter}
            pathFilter={pathFilter}
            setPathFilter={setPathFilter}
            keyFilter={keyFilter}
            setKeyFilter={setKeyFilter}
            accounts={accounts}
            keys={keys}
            hidePolls={hidePolls}
            setHidePolls={setHidePolls}
            offset={offset}
            setOffset={setOffset}
            loading={loading}
            setDetail={setDetail}
          />
        )}
        {page === 4 && (
          <KeysPage
            keys={keys}
            builtin={builtin}
            setKeyEdit={setKeyEdit}
            setLabel={setLabel}
            setNote={setNote}
            ask={ask}
            api={api}
            openModal={openModal}
          />
        )}
      </main>
      <ConsoleModals
        api={api}
        busy={busy}
        act={act}
        notify={notify}
        copy={copy}
        onRefresh={() => setRefresh((v) => v + 1)}
        accounts={accounts}
        pool={pool}
        devicesOpen={devicesOpen}
        setDevicesOpen={setDevicesOpen}
        modal={modal}
        setModal={setModal}
        edit={edit}
        setEdit={setEdit}
        maxConcurrency={maxConcurrency}
        setMaxConcurrency={setMaxConcurrency}
        mergeSource={mergeSource}
        setMergeSource={setMergeSource}
        mergeTarget={mergeTarget}
        setMergeTarget={setMergeTarget}
        keyEdit={keyEdit}
        setKeyEdit={setKeyEdit}
        label={label}
        setLabel={setLabel}
        cookies={cookies}
        setCookies={setCookies}
        note={note}
        setNote={setNote}
        content={content}
        setContent={setContent}
        preview={preview}
        setPreview={setPreview}
        imported={imported}
        setImported={setImported}
        secret={secret}
        setSecret={setSecret}
        session={session}
        setSession={setSession}
        detail={detail}
        setDetail={setDetail}
        confirm={confirm}
        setConfirm={setConfirm}
      />
      {toast && (
        <div className="toast" role="status">
          {toast}
          <button aria-label="关闭通知" onClick={() => setToast("")}>
            ×
          </button>
        </div>
      )}
    </div>
  );
}
