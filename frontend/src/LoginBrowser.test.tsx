import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Api } from "./api";
import { LoginBrowser } from "./LoginBrowser";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
describe("远程网页登录", () => {
  it("鉴权拉取截图、转发文本，验证失败不关闭，并在卸载取消", async () => {
    const calls: { path: string; body: unknown; method: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        expect((init.headers as Record<string, string>).Authorization).toBe(
          "Bearer admin-test",
        );
        calls.push({
          path,
          body: init.body ? JSON.parse(String(init.body)) : null,
          method: String(init.method),
        });
        if (path.endsWith("/complete"))
          return new Response(
            JSON.stringify({ error: { message: "请先完成验证码" } }),
            { status: 400 },
          );
        return new Response(
          JSON.stringify(
            path.endsWith("/frame")
              ? { image: "test-jpeg", width: 1280, height: 900 }
              : {
                  id: "s1",
                  status: "pending",
                  expires_at: Date.now() / 1000 + 600,
                  width: 1280,
                  height: 900,
                },
          ),
        );
      }),
    );
    const close = vi.fn(),
      complete = vi.fn();
    const user = userEvent.setup();
    const view = render(
      <LoginBrowser
        api={new Api("admin-test")}
        session={{
          id: "s1",
          status: "pending",
          expires_at: Date.now() / 1000 + 600,
          width: 1280,
          height: 900,
        }}
        onClose={close}
        onComplete={complete}
        notify={vi.fn()}
      />,
    );
    await screen.findByAltText(/实时截图/);
    const textInput = screen.getByLabelText(
      "发送到上游页面的文本",
    ) as HTMLInputElement;
    expect(textInput.type).toBe("password");
    await user.click(screen.getByRole("button", { name: "显示文本" }));
    expect(textInput.type).toBe("text");
    await user.click(screen.getByRole("button", { name: "隐藏文本" }));
    expect(textInput.type).toBe("password");
    await user.type(textInput, "private-text");
    await user.click(screen.getByRole("button", { name: "发送到页面" }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => (c.body as { text?: string })?.text === "private-text",
        ),
      ).toBe(true),
    );
    expect(
      (screen.getByLabelText("发送到上游页面的文本") as HTMLInputElement).value,
    ).toBe("");
    await user.click(screen.getByRole("button", { name: "Esc" }));
    await waitFor(() =>
      expect(
        calls.some((c) => (c.body as { key?: string })?.key === "Escape"),
      ).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "完成验证并入池" }));
    await screen.findByText("请先完成验证码");
    expect(complete).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    view.unmount();
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.path === "/admin/login-sessions/s1" && c.method === "DELETE",
        ),
      ).toBe(true),
    );
  });
  it("过期会话不获取截图并禁止提交", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            id: "s2",
            status: "expired",
            expires_at: 1,
            width: 1280,
            height: 900,
          }),
        ),
    );
    vi.stubGlobal("fetch", fetcher);
    render(
      <LoginBrowser
        api={new Api("test")}
        session={{
          id: "s2",
          status: "expired",
          expires_at: 1,
          width: 1280,
          height: 900,
        }}
        onClose={vi.fn()}
        onComplete={vi.fn()}
        notify={vi.fn()}
      />,
    );
    expect(screen.getByText(/登录会话已过期/)).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "完成验证并入池",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  });
});

it("自动完成只通知一次，卸载不再 DELETE 或请求截图", async () => {
  const fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          id: "auto",
          status: "completed",
          expires_at: Date.now() / 1000 + 60,
          width: 1280,
          height: 900,
        }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const complete = vi.fn(),
    notify = vi.fn();
  const view = render(
    <LoginBrowser
      api={new Api("test")}
      session={{
        id: "auto",
        status: "pending",
        expires_at: Date.now() / 1000 + 600,
        width: 1280,
        height: 900,
      }}
      onClose={vi.fn()}
      onComplete={complete}
      notify={notify}
    />,
  );
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
  expect(notify).toHaveBeenCalledTimes(1);
  window.dispatchEvent(new Event("pagehide"));
  view.unmount();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
