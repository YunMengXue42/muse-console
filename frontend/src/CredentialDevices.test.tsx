import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Api, type Account } from "./api";
import { CredentialDevices } from "./CredentialDevices";

const account = { id: "account-1" } as Account;
const devices = [
  {
    id: "device/1",
    expires_at: Date.now() / 1000 + 3600,
    revoked: false,
    label: "test-device",
    new_account_limit: 1,
  },
  { id: "revoked", expires_at: 1, revoked: true },
];
function setup(
  options: {
    disabled?: boolean;
    failure?: boolean;
    expired?: boolean;
    legacy?: boolean;
  } = {},
) {
  const fetcher = vi.fn(async (path: string, init: RequestInit) => {
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer admin-test",
    );
    if (options.disabled) return new Response("{}", { status: 404 });
    if (path.endsWith("/pairings"))
      return new Response(
        JSON.stringify({
          challenge: "one-time-secret",
          server_id: "server-1",
          fingerprint: "app-fingerprint",
          server_origin: "https://muse.example",
          expires_at: options.expired ? 1 : Date.now() / 1000 + 300,
        }),
      );
    if (init.method === "POST" || init.method === "DELETE")
      return new Response(
        JSON.stringify(
          options.failure
            ? { detail: "cookie=secret; subject=private" }
            : { subject: "must-not-render" },
        ),
        { status: options.failure ? 409 : 200 },
      );
    return new Response(
      JSON.stringify(
        options.legacy
          ? devices.map(({ id, new_account_limit, ...rest }) => ({
              ...rest,
              device_id: id,
              new_account_quota: new_account_limit,
            }))
          : { data: devices },
      ),
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const close = vi.fn();
  const view = render(
    <CredentialDevices
      api={new Api("admin-test")}
      accounts={[account]}
      onClose={close}
    />,
  );
  return { fetcher, close, view, user: userEvent.setup() };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
describe("扩展设备配对", () => {
  it("兼容旧数组和 device_id 字段", async () => {
    setup({ legacy: true });
    await screen.findByRole("button", { name: "撤销设备 device/1" });
    expect(screen.getByText("已撤销")).toBeTruthy();
  });
  it("只在点击后配对，直接展示规范响应身份且不额外 fetch、不持久化或复制挑战", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const { fetcher, user, view } = setup();
    await screen.findByRole("button", { name: "撤销设备 device/1" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/非 TLS 证书指纹/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "生成配对挑战" }));
    expect(await screen.findByDisplayValue("one-time-secret")).toBeTruthy();
    expect(screen.getByText("https://muse.example")).toBeTruthy();
    expect(screen.getByText("server-1")).toBeTruthy();
    expect(screen.getByText("app-fingerprint")).toBeTruthy();
    expect(screen.getByText("test-device")).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(2);
    const request = fetcher.mock.calls.find(([path]) =>
      path.endsWith("/pairings"),
    );
    expect(request?.[0]).toBe("/admin/credential-devices/pairings");
    expect(JSON.parse(String(request?.[1].body))).toEqual({
      label: "",
      new_account_limit: 1,
    });
    expect(storage).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /复制/ })).toBeNull();
    view.unmount();
    expect(screen.queryByDisplayValue("one-time-secret")).toBeNull();
  });
  it("404 友好说明，不影响关闭或生成动作", async () => {
    const { user, close } = setup({ disabled: true });
    await screen.findByText(/需管理员开启/);
    await user.click(screen.getByRole("button", { name: "生成配对挑战" }));
    await screen.findByText(/账号池其他功能不受影响/);
    await user.click(screen.getByRole("button", { name: "关闭配对窗口" }));
    expect(close).toHaveBeenCalledTimes(1);
  });
  it("撤销前确认，可取消，成功后标记撤销且不展示返回秘密", async () => {
    const { fetcher, user } = setup();
    const button = await screen.findByRole("button", {
      name: "撤销设备 device/1",
    });
    await user.click(button);
    expect(
      fetcher.mock.calls.filter(([, init]) => init.method !== "GET"),
    ).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "取消操作" }));
    await user.click(button);
    await user.click(screen.getByRole("button", { name: "确认撤销" }));
    await screen.findByText("设备已撤销。");
    expect(
      fetcher.mock.calls.some(
        ([path, init]) =>
          path === "/admin/credential-devices/device%2F1" &&
          init.method === "DELETE",
      ),
    ).toBe(true);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("must-not-render")).toBeNull();
  });
  it("身份或版本冲突确认仅针对原 capture 和目标当前版本，API 不携带版本改写", async () => {
    const { fetcher, user } = setup();
    await screen.findByRole("button", { name: "撤销设备 device/1" });
    await user.selectOptions(
      screen.getByLabelText("设备 device_id"),
      "device/1",
    );
    await user.type(screen.getByLabelText("候选 capture_id"), " capture-1 ");
    await user.selectOptions(
      screen.getByLabelText("目标 account_id"),
      "account-1",
    );
    await user.click(screen.getByRole("button", { name: "核对本次候选" }));
    expect(
      fetcher.mock.calls.filter(([, init]) => init.method !== "GET"),
    ).toHaveLength(0);
    expect(
      screen.getByRole("heading", { name: "确认本次身份冲突？" }),
    ).toBeTruthy();
    expect(screen.getByText(/只对本次候选生效/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "仅确认本次候选" }));
    const result = await screen.findByText(/已确认，尚未入池/);
    expect(result.textContent).toContain("在扩展重传同一候选");
    const request = fetcher.mock.calls.find(([path]) =>
      path.endsWith("/confirm"),
    );
    expect(request?.[0]).toBe("/admin/credential-devices/device%2F1/confirm");
    expect(JSON.parse(String(request?.[1].body))).toEqual({
      capture_id: "capture-1",
      account_id: "account-1",
    });
    expect(
      (screen.getByLabelText("候选 capture_id") as HTMLInputElement).value,
    ).toBe("");
    expect(screen.queryByText("must-not-render")).toBeNull();
  });
  it("失败保持确认上下文但不渲染服务端原始错误载荷", async () => {
    const { user } = setup({ failure: true });
    await user.click(
      await screen.findByRole("button", { name: "撤销设备 device/1" }),
    );
    await user.click(screen.getByRole("button", { name: "确认撤销" }));
    await screen.findByText(/操作失败/);
    expect(screen.getByRole("button", { name: "确认撤销" })).toBeTruthy();
    expect(screen.queryByText(/cookie=secret/)).toBeNull();
  });
  it("过期挑战不显示秘密", async () => {
    const next = setup({ expired: true });
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "生成配对挑战",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    await next.user.click(screen.getByRole("button", { name: "生成配对挑战" }));
    await screen.findByText("配对挑战已过期，请重新生成。");
    expect(screen.queryByDisplayValue("one-time-secret")).toBeNull();
  });
  it("支持自定义配对设备备注与新增账号额度", async () => {
    const { fetcher, user } = setup();
    await screen.findByRole("button", { name: "撤销设备 device/1" });
    await user.type(
      screen.getByLabelText("配对设备备注（可选）"),
      "  办公电脑  ",
    );
    const limitInput = screen.getByLabelText("自动新增账号额度（0–10）");
    await user.clear(limitInput);
    await user.type(limitInput, "3");
    await user.click(screen.getByRole("button", { name: "生成配对挑战" }));
    await screen.findByDisplayValue("one-time-secret");
    const request = fetcher.mock.calls.find(([path]) =>
      path.endsWith("/pairings"),
    );
    expect(JSON.parse(String(request?.[1].body))).toEqual({
      label: "办公电脑",
      new_account_limit: 3,
    });
  });
});
