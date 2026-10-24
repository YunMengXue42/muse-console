import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
    const heading = screen.getByRole("heading", { name: "Muse Console" });
    const language = screen.getByRole("group", { name: "界面语言" });
    expect(language.parentElement?.className).toBe("login-card-tools");
    expect(language.parentElement?.nextElementSibling).toBe(heading);
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
    expect(screen.getByRole("status").textContent).toContain("在线");
    const indicator = screen.getByRole("status").querySelector(".dot.online");
    expect(indicator?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByRole("status").textContent).toContain("vtest");
    const originalTheme = document.documentElement.dataset.theme;
    await user.click(screen.getByRole("button", { name: "切换主题" }));
    expect(document.documentElement.dataset.theme).not.toBe(originalTheme);
    expect(sessionStorage.getItem("muse.console.key")).toBe("admin-test");
    expect(screen.queryByRole("button", { name: "退出控制台" })).toBeNull();
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
  it("登录会话仅保存在 sessionStorage", async () => {
    await signIn();
    expect(sessionStorage.getItem("muse.console.key")).toBe("admin-test");
    expect(localStorage.getItem("muse.console.key")).toBeNull();
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
  it("打通后端多维统计、任务媒体预览、请求筛选与账号并发/合并操作", async () => {
    const baseApi = mockApi();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (
          path === "/admin/accounts" &&
          (!init?.method || init.method === "GET")
        ) {
          return new Response(
            JSON.stringify({
              data: [
                {
                  id: "acc_main",
                  label: "主力账号",
                  enabled: true,
                  status: "active",
                  effective_status: "available",
                  inflight: 0,
                  max_concurrency: 2,
                  effective_max_concurrency: 2,
                  credential_version: 3,
                  success_count: 10,
                  fail_count: 0,
                  last_used_at: 1700000000,
                },
                {
                  id: "acc_dup",
                  label: "重复账号",
                  enabled: true,
                  status: "active",
                  effective_status: "available",
                  inflight: 0,
                  effective_max_concurrency: 2,
                  success_count: 1,
                  fail_count: 0,
                  last_used_at: null,
                },
              ],
            }),
            { headers: { "Content-Type": "application/json" } },
          );
        }
        if (path.startsWith("/admin/stats?")) {
          return new Response(
            JSON.stringify({
              total: 5,
              errors: 1,
              server_errors: 1,
              error_rate: 0.2,
              latency_ms: { p50: 40, p95: 120 },
              series: [{ t: 1700000000, count: 5, errors: 1 }],
              by_model: [{ model: "muse-video", count: 5, errors: 1 }],
              by_account: [{ account_id: "acc_main", count: 5, errors: 1 }],
              by_key: [
                {
                  key_id: "key_prod",
                  key_name: "生产画布",
                  count: 5,
                  errors: 1,
                },
              ],
              by_status: [{ status_code: 200, count: 4, errors: 0 }],
            }),
            { headers: { "Content-Type": "application/json" } },
          );
        }
        if (path === "/admin/metrics") {
          const prom = [
            "# HELP muse_chat_first_token_seconds Seconds from lease to first chat delta.",
            "# TYPE muse_chat_first_token_seconds histogram",
            'muse_chat_first_token_seconds_sum{account="acc_main"} 6.4',
            'muse_chat_first_token_seconds_count{account="acc_main"} 2',
            "# HELP muse_generation_seconds Media generation wall time.",
            "# TYPE muse_generation_seconds histogram",
            'muse_generation_seconds_sum{kind="video",result="ok"} 150',
            'muse_generation_seconds_count{kind="video",result="ok"} 2',
            "# HELP muse_media_outcomes_total Browser media generation events by kind and outcome code.",
            "# TYPE muse_media_outcomes_total counter",
            'muse_media_outcomes_total{kind="video",outcome="retry_text_only"} 2',
            'muse_media_outcomes_total{kind="video",outcome="succeeded_after_retry"} 1',
            "# HELP muse_upstream_requests_total Gateway upstream calls by operation, account and result.",
            "# TYPE muse_upstream_requests_total counter",
            'muse_upstream_requests_total{account="acc_main",op="video",result="ok"} 2',
            "",
          ].join("\n");
          return new Response(prom, {
            headers: { "Content-Type": "text/plain; version=0.0.4" },
          });
        }
        if (path === "/admin/tasks") {
          return new Response(
            JSON.stringify({
              data: [
                {
                  id: "task_vid_1",
                  status: "succeeded",
                  kind: "video",
                  progress: 100,
                  created_at: 1700000000,
                  updated_at: 1700000075,
                  request: {
                    prompt: "海边日落航拍镜头",
                    size: "1280x720",
                    duration: 10,
                  },
                  result: {
                    url: "http://localhost/v1/media/vid_1234567890abcdef.mp4?expires=9999999999&signature=abc",
                    mime: "video/mp4",
                  },
                  error: null,
                },
              ],
            }),
            { headers: { "Content-Type": "application/json" } },
          );
        }
        return baseApi(input, init);
      }),
    );

    const user = await signIn();
    // 账号编辑并发上限与合并
    await user.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    const mcInput = screen.getByLabelText(
      "单账号并发上限（可选）",
    ) as HTMLInputElement;
    expect(mcInput.value).toBe("2");
    await user.clear(mcInput);
    await user.type(mcInput, "4");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/accounts/acc_main",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ label: "主力账号", max_concurrency: 4 }),
        }),
      ),
    );

    await user.click(screen.getAllByRole("button", { name: "合并" })[1]);
    await user.selectOptions(screen.getByLabelText("目标账号"), "acc_main");
    await user.click(screen.getByRole("button", { name: "确认合并" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/accounts/acc_dup/merge",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ into: "acc_main" }),
        }),
      ),
    );

    // 概览多维分布、0.3.5 Prometheus 实时指标与点击跳转请求日志
    await user.click(screen.getByRole("button", { name: "调用概览" }));
    await screen.findByText("5xx 错误 1 次");
    await screen.findByText("3.20 s");
    expect(screen.getByText("75.0 s")).toBeTruthy();
    expect(screen.getByText("重投后成功 1 次")).toBeTruthy();
    expect(screen.getByText("只回文本重投")).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "查看 Prometheus 指标" }),
    );
    const metricsDialog = screen.getByRole("dialog", {
      name: "Prometheus 指标 (/admin/metrics)",
    });
    await user.click(
      within(metricsDialog).getAllByRole("button", { name: "关闭" })[0],
    );
    await screen.findByText("生产画布");
    await user.click(screen.getByRole("button", { name: "生产画布" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/requests?hide_polls=true&limit=50&offset=0&key_id=key_prod",
        expect.anything(),
      ),
    );

    // 任务中心 Prompt、耗时与媒体内嵌预览
    await user.click(screen.getByRole("button", { name: "任务中心" }));
    await screen.findByText("海边日落航拍镜头");
    expect(screen.getByText("耗时 1m 15s")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "结果详情" }));
    const dialog = screen.getByRole("dialog", { name: "任务 task_vid_1" });
    const video = dialog.querySelector("video");
    expect(video?.getAttribute("src")).toBe(
      "/v1/media/vid_1234567890abcdef.mp4?expires=9999999999&signature=abc",
    );
  });
  it("登录态支持 URL Hash 状态恢复与同步，并在标签页切回前台时立即刷新", async () => {
    sessionStorage.setItem("muse.console.key", "admin-test");
    window.history.replaceState(
      null,
      "",
      "#requests?status=failed&account_id=acc_hash",
    );
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "请求记录" });
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/admin/requests?hide_polls=true&limit=50&offset=0&status=failed&account_id=acc_hash",
        expect.anything(),
      ),
    );
    await user.click(screen.getByRole("button", { name: "调用概览" }));
    await user.click(screen.getByRole("button", { name: "近 7 天" }));
    expect(window.location.hash).toBe("#overview?window=7d");
    const callCountBeforeVisible = (fetch as ReturnType<typeof vi.fn>).mock
      .calls.length;
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() =>
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThan(callCountBeforeVisible),
    );
  });
});
