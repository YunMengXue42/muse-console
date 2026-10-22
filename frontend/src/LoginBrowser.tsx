import { useEffect, useRef, useState } from "react";
import { Api, ApiError, framePoint, time, type LoginSession } from "./api";
import { Modal } from "./ui";
import "./login-browser.css";
/** 登录截图仅通过带 Bearer 的 API 获取；顺序轮询避免截图请求重叠。 */
export function LoginBrowser({
  api,
  session,
  onClose,
  onComplete,
  notify,
}: {
  api: Api;
  session: LoginSession;
  onClose: () => void;
  onComplete: () => void;
  notify: (message: string) => void;
}) {
  const [current, setCurrent] = useState(session),
    [image, setImage] = useState(""),
    [text, setText] = useState(""),
    [masked, setMasked] = useState(true),
    [now, setNow] = useState(() => Date.now()),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const active = useRef(true),
    done = useRef(false),
    inputLock = useRef(false);
  const callbacks = useRef({ notify, onComplete });
  callbacks.current = { notify, onComplete };
  function finish() {
    if (done.current) return;
    done.current = true;
    callbacks.current.notify("已验证登录并加入账号池");
    callbacks.current.onComplete();
  }
  const expiry =
    typeof current.expires_at === "number"
      ? current.expires_at * 1000
      : Date.parse(current.expires_at);
  const expired = current.status === "expired" || now >= expiry;
  const remainingSec = Math.max(0, Math.ceil((expiry - now) / 1000));
  useEffect(() => {
    if (expired) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [expired]);
  useEffect(() => {
    active.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    let terminal = false;
    const cancel = () => {
      active.current = false;
      clearTimeout(timer);
      controller.abort();
      if (!done.current) {
        done.current = true;
        void api
          .request(
            `/login-sessions/${session.id}`,
            "DELETE",
            undefined,
            undefined,
            true,
          )
          .catch(() => {});
      }
    };
    window.addEventListener("pagehide", cancel);
    async function poll() {
      try {
        const state = await api.request<LoginSession>(
          `/login-sessions/${session.id}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (!active.current) return;
        setCurrent(state);
        if (state.status === "completed") {
          terminal = true;
          finish();
          return;
        }
        if (done.current) return;
        const until =
          typeof state.expires_at === "number"
            ? state.expires_at * 1000
            : Date.parse(state.expires_at);
        if (
          ["expired", "cancelled", "completed"].includes(state.status) ||
          Date.now() >= until
        ) {
          terminal = true;
          return;
        }
        const frame = await api.request<{
          image: string;
          width: number;
          height: number;
        }>(
          `/login-sessions/${session.id}/frame`,
          "GET",
          undefined,
          controller.signal,
        );
        if (active.current) {
          setImage(
            frame.image.startsWith("data:")
              ? frame.image
              : `data:image/jpeg;base64,${frame.image}`,
          );
          setCurrent({ ...state, width: frame.width, height: frame.height });
          setError("");
        }
      } catch (e) {
        if (active.current) {
          setError((e as Error).message);
          if (e instanceof ApiError && [404, 410].includes(e.status)) {
            terminal = true;
            setCurrent((value) => ({ ...value, status: "expired" }));
          }
        }
      } finally {
        if (active.current && !terminal && !done.current)
          timer = setTimeout(() => void poll(), 1500);
      }
    }
    void poll();
    return () => {
      window.removeEventListener("pagehide", cancel);
      cancel();
    };
  }, [api, session.id]);
  /** 逐个转发输入，不保存文本；服务器失败时保持窗口便于重试。 */
  async function input(payload: unknown) {
    if (inputLock.current || expired || done.current) return;
    inputLock.current = true;
    setBusy(true);
    try {
      await api.request(`/login-sessions/${session.id}/input`, "POST", payload);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inputLock.current = false;
      setBusy(false);
    }
  }
  async function complete() {
    if (inputLock.current || done.current) return;
    setBusy(true);
    inputLock.current = true;
    try {
      await api.request(`/login-sessions/${session.id}/complete`, "POST", {});
      finish();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inputLock.current = false;
      setBusy(false);
    }
  }
  const statusText = expired
    ? "已过期"
    : (STATUS_LABEL[current.status] ?? current.status);
  const low = !expired && remainingSec <= 60;
  return (
    <Modal
      title="上游真实网页登录"
      wide
      className="login-browser"
      onClose={onClose}
    >
      <div className="lb-body">
        <div className="lb-stage">
          {expired ? (
            <p role="alert" className="error">
              登录会话已过期，请关闭并重新发起登录。
            </p>
          ) : image ? (
            <img
              className="browser-frame"
              src={image}
              alt="muse.ai 登录页面实时截图，点击转发到真实页面"
              draggable={false}
              onClick={(e) =>
                void input({
                  type: "click",
                  ...framePoint(
                    e.clientX,
                    e.clientY,
                    e.currentTarget.getBoundingClientRect(),
                    current.width,
                    current.height,
                  ),
                })
              }
              onWheel={(e) => {
                if (Math.abs(e.deltaY) >= 1)
                  void input({
                    type: "scroll",
                    delta_y: e.deltaY > 0 ? 500 : -500,
                  });
              }}
            />
          ) : (
            <div className="empty">正在获取上游页面截图…</div>
          )}
        </div>
        <aside className="lb-panel">
          <div className="lb-session">
            <span className={`lb-chip ${expired ? "bad" : ""}`}>
              {statusText}
            </span>
            <span
              className={`lb-timer ${low ? "low" : ""}`}
              title={`到期 ${time(current.expires_at)}`}
            >
              {expired ? "--:--" : clock(remainingSec)}
            </span>
          </div>
          <ol className="lb-steps">
            <li>点截图里的输入框</li>
            <li>在下方输入并发送</li>
            <li>登录成功后点「完成验证并入池」</li>
          </ol>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <form
            className="lb-text"
            onSubmit={(e) => {
              e.preventDefault();
              const value = text;
              setText("");
              void input({ type: "text", text: value });
            }}
          >
            <input
              aria-label="发送到上游页面的文本"
              type={masked ? "password" : "text"}
              autoComplete="off"
              value={text}
              disabled={expired}
              onChange={(e) => setText(e.target.value)}
              placeholder="邮箱、手机号或验证码"
            />
            <div className="lb-row">
              <button
                type="button"
                onClick={() => setMasked((v) => !v)}
                aria-pressed={!masked}
              >
                {masked ? "显示文本" : "隐藏文本"}
              </button>
              <button className="primary" disabled={busy || !text || expired}>
                发送到页面
              </button>
            </div>
          </form>
          <div className="lb-keys" aria-label="按键">
            {KEYS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                disabled={busy || expired}
                onClick={() => void input({ type: "key", key })}
              >
                {label}
              </button>
            ))}
            <button
              type="button"
              aria-label="向上滚动"
              title="向上滚动"
              disabled={busy || expired}
              onClick={() => void input({ type: "scroll", delta_y: -500 })}
            >
              上翻
            </button>
            <button
              type="button"
              aria-label="向下滚动"
              title="向下滚动"
              disabled={busy || expired}
              onClick={() => void input({ type: "scroll", delta_y: 500 })}
            >
              下翻
            </button>
          </div>
          <p className="lb-note">
            输入只转发、不在本地保存。截图上可滚轮翻页。
          </p>
          <footer>
            <button onClick={onClose}>取消登录</button>
            <button
              className="primary"
              disabled={busy || expired || current.status === "completed"}
              onClick={() => void complete()}
            >
              {busy ? "处理中…" : "完成验证并入池"}
            </button>
          </footer>
        </aside>
      </div>
    </Modal>
  );
}
const KEYS = [
  ["Enter", "Enter"],
  ["Tab", "Tab"],
  ["Backspace", "退格"],
  ["Escape", "Esc"],
  ["ArrowUp", "↑"],
  ["ArrowDown", "↓"],
  ["ArrowLeft", "←"],
  ["ArrowRight", "→"],
] as const;
const STATUS_LABEL: Record<string, string> = {
  pending: "等待操作",
  awaiting_login: "等待登录",
  retry_wait: "正在重试",
  manual_required: "需手动完成",
  completed: "已完成",
  cancelled: "已取消",
  expired: "已过期",
};
function clock(sec: number) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
