import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
const status = {
  version: "test",
  driver: { driver: "mock", ok: true },
  accounts: {
    total: 0,
    available: 0,
    inflight: 0,
    invalid: 0,
    cooling: 0,
    disabled: 0,
  },
};
let imports: boolean[];
/** 测试边界只提供明确的 API 固定响应，不向生产注入模拟数据。 */
function mockApi() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    let body: unknown = {};
    if (path === "/admin/status") body = status;
    else if (path === "/admin/accounts") body = { data: [] };
    else if (path === "/admin/pool")
      body = {
        strategy: "round_robin",
        max_concurrency: 2,
        cooldown: 30,
        acquire_timeout: 60,
      };
    else if (path === "/admin/accounts/import") {
      const preview = JSON.parse(String(init?.body)).preview;
      imports.push(preview);
      body = {
        data: [
          {
            index: 1,
            label: "测试导入",
            status: preview ? "ready" : "imported",
          },
          {
            index: 2,
            label: "错误记录",
            status: "error",
            message: "缺少 session cookie",
          },
        ],
        summary: {
          ready: preview ? 1 : 0,
          imported: preview ? 0 : 1,
          duplicate: 0,
          error: 1,
        },
      };
    } else if (path === "/admin/keys" && init?.method === "POST")
      body = { api_key: "one-time-test-key" };
    else if (path === "/admin/keys") body = { data: [], builtin: [] };
    else if (path.startsWith("/admin/stats?"))
      body = {
        total: 2,
        errors: 1,
        error_rate: 0.5,
        latency_ms: { p50: 20, p95: 30 },
        series: [{ t: 1, count: 2, errors: 1 }],
        by_model: [{ model: "real-contract-test", count: 2, errors: 1 }],
      };
    else if (path === "/admin/tasks")
      body = {
        data: [
          {
            id: "task-test",
            status: "running",
            kind: "video",
            created_at: 1,
            result: null,
            error: null,
          },
        ],
      };
    else if (path.endsWith("/cancel")) body = { cancelled: true };
    else if (path.startsWith("/admin/requests?")) body = { data: [], total: 0 };
    else if (path === "/admin/login-sessions")
      return new Response(
        JSON.stringify({ error: { message: "mock 驱动不支持网页登录" } }),
        { status: 501 },
      );
    return new Response(JSON.stringify(body), {
      headers: { "Content-Type": "application/json" },
    });
  });
}
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  imports = [];
  vi.stubGlobal("fetch", mockApi());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function signIn() {
  const user = userEvent.setup();
  render(<App />);
  await user.type(screen.getByLabelText("管理员 Key"), "admin-test");
  await user.click(screen.getByRole("button", { name: "进入控制台" }));
  await screen.findByRole("heading", { name: "上游账号" });
  return user;
}
describe("控制台完整交互", () => {
  it("登录页只保留单个密钥提示与底部短说明", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Muse Console" })).toBeTruthy();
    expect(
      screen.getByLabelText("管理员 Key").getAttribute("placeholder"),
    ).toBe("管理员 Key");
    expect(screen.queryByText("管理员 Key")).toBeNull();
    expect(screen.queryByText("YOUR PRIVATE WORKSPACE")).toBeNull();
    expect(screen.queryByText(/sessionStorage/)).toBeNull();
    const caption = screen.getByText("管理账号与 API 调用");
    expect(caption.parentElement?.lastElementChild).toBe(caption);
  });
  it("概览窗口、任务取消确认与日志筛选发出真实契约请求", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "调用概览" }));
    await screen.findByText("real-contract-test");
    await user.click(screen.getByRole("button", { name: "近 1 小时" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/stats?window=1h",
        expect.anything(),
      ),
    );
    await user.click(screen.getByRole("button", { name: "任务中心" }));
    await screen.findByText("task-test");
    await user.click(screen.getByRole("button", { name: "取消任务" }));
    expect(screen.getByRole("dialog", { name: "确认操作" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^确认$/ }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/tasks/task-test/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "请求日志" }));
    await user.selectOptions(screen.getByLabelText("响应状态"), "failed");
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/requests?hide_polls=true&limit=50&offset=0&status=failed",
        expect.anything(),
      ),
    );
  });
  it("实例状态保持可读，主题切换不影响登录", async () => {
    const user = await signIn();
    expect(screen.getByText("驱动在线")).toBeTruthy();
    const indicator = screen.getByRole("status").querySelector(".dot.online");
    expect(indicator?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByText("vtest")).toBeTruthy();
    const originalTheme = document.documentElement.dataset.theme;
    await user.click(screen.getByRole("button", { name: "切换主题" }));
    expect(document.documentElement.dataset.theme).not.toBe(originalTheme);
    expect(sessionStorage.getItem("muse.console.key")).toBe("admin-test");
    expect(screen.getByRole("button", { name: "退出控制台" })).toBeTruthy();
  });
  it("驱动异常时不显示在线动效标记", async () => {
    const api = mockApi();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
        String(input) === "/admin/status"
          ? Promise.resolve(
              new Response(
                JSON.stringify({
                  ...status,
                  driver: { ...status.driver, ok: false },
                }),
                { headers: { "Content-Type": "application/json" } },
              ),
            )
          : api(input, init),
      ),
    );
    await signIn();
    expect(screen.getByText("驱动异常")).toBeTruthy();
    expect(screen.getByRole("status").querySelector(".online")).toBeNull();
  });
  it("登录会话仅保存在 sessionStorage，退出清理", async () => {
    const user = await signIn();
    expect(sessionStorage.getItem("muse.console.key")).toBe("admin-test");
    expect(localStorage.getItem("muse.console.key")).toBeNull();
    await user.click(screen.getByRole("button", { name: "退出控制台" }));
    expect(sessionStorage.getItem("muse.console.key")).toBeNull();
    expect(screen.getByLabelText("管理员 Key")).toBeTruthy();
  });
  it("导入必须预览再确认，并保留逐条错误", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "批量导入 Cookie" }));
    await user.type(screen.getByLabelText("Cookie 内容"), "cookie-content");
    expect(
      (screen.getByRole("button", { name: "确认入池" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "预览检查" }));
    await screen.findByText("缺少 session cookie");
    await user.click(screen.getByRole("button", { name: "确认入池" }));
    await waitFor(() => expect(imports).toEqual([true, false]));
    expect(screen.getByText("缺少 session cookie")).toBeTruthy();
  });
  it("不支持网页登录时保留弹窗并显示真实错误", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "＋ 网页登录" }));
    await user.click(screen.getByRole("button", { name: "打开网页登录" }));
    await waitFor(() =>
      expect(
        screen.getAllByText("mock 驱动不支持网页登录").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.getByRole("dialog", { name: "连接上游账号" })).toBeTruthy();
    expect(screen.queryByAltText(/实时截图/)).toBeNull();
  });
  it("新密钥只显示一次，关闭后不出现在列表", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "API 密钥" }));
    await user.click(screen.getByRole("button", { name: "＋ 创建密钥" }));
    await user.type(screen.getByLabelText("密钥名称"), "app");
    await user.click(screen.getByRole("button", { name: /^创建密钥$/ }));
    await screen.findByText("one-time-test-key");
    await user.click(screen.getByRole("button", { name: "已安全保存" }));
    expect(screen.queryByText("one-time-test-key")).toBeNull();
  });
});
