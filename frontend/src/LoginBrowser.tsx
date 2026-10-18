import { useEffect, useRef, useState } from "react";
import { Api, ApiError, framePoint, time, type LoginSession } from "./api";
import { Modal } from "./ui";
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
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const active = useRef(true),
    done = useRef(false),
    inputLock = useRef(false);
  const expiry =
    typeof current.expires_at === "number"
      ? current.expires_at * 1000
      : Date.parse(current.expires_at);
  const expired = current.status === "expired" || Date.now() >= expiry;
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
        if (active.current && !terminal)
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
    if (inputLock.current || expired) return;
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
    if (inputLock.current) return;
    setBusy(true);
    inputLock.current = true;
    try {
      await api.request(`/login-sessions/${session.id}/complete`, "POST", {});
      done.current = true;
      notify("已验证登录并加入账号池");
      onComplete();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inputLock.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal title="上游真实网页登录" wide onClose={onClose}>
      <p className="muted">
        这是 muse.ai
        的真实网页登录。请逐号登录，并手动完成验证码。输入内容不在本地保存。
      </p>
      <div className="session-meta">
        <span>会话 {current.status}</span>
        <span>到期 {time(current.expires_at)}</span>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {expired ? (
        <p role="alert" className="error">
          登录会话已过期，请关闭并重新发起登录。
        </p>
      ) : (
        <>
          {image ? (
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
            />
          ) : (
            <div className="empty">正在获取上游页面截图…</div>
          )}
          <form
            className="toolbar"
            onSubmit={(e) => {
              e.preventDefault();
              const value = text;
              setText("");
              void input({ type: "text", text: value });
            }}
          >
            <input
              aria-label="发送到上游页面的文本"
              type="password"
              autoComplete="off"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="先点击页面中的输入框，再输入文本"
            />
            <button disabled={busy || !text}>发送到页面</button>
          </form>
          <div className="toolbar">
            {["Enter", "Tab", "Backspace"].map((key) => (
              <button
                key={key}
                disabled={busy}
                onClick={() => void input({ type: "key", key })}
              >
                {key === "Backspace" ? "退格" : key}
              </button>
            ))}
            <button
              disabled={busy}
              onClick={() => void input({ type: "scroll", delta_y: -500 })}
            >
              向上滚动
            </button>
            <button
              disabled={busy}
              onClick={() => void input({ type: "scroll", delta_y: 500 })}
            >
              向下滚动
            </button>
          </div>
        </>
      )}
      <footer>
        <button onClick={onClose}>取消登录</button>
        <button
          className="primary"
          disabled={busy || expired}
          onClick={() => void complete()}
        >
          {busy ? "处理中…" : "完成验证并入池"}
        </button>
      </footer>
    </Modal>
  );
}
