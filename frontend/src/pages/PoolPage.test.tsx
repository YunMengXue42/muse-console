import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Api, type Account } from "../api";
import { PoolPage } from "./PoolPage";

const account = (id: string, effective_status?: string): Account => ({
  id,
  label: `账号 ${id}`,
  enabled: true,
  status: "active",
  effective_status,
  inflight: 0,
  effective_max_concurrency: 2,
  success_count: 3,
  fail_count: 1,
  last_used_at: null,
});
const accounts = [
  account("alpha", "available"),
  account("beta", "invalid"),
  account("gamma", "busy"),
  account("delta", "cooldown"),
  account("epsilon", "disabled"),
  account("fallback"),
];
function setup(
  options: { accounts?: Account[]; busy?: boolean; pool?: boolean } = {},
) {
  const api = new Api("test");
  const request = vi.spyOn(api, "request").mockResolvedValue({});
  const ask = vi.fn();
  const setEdit = vi.fn();
  const setLabel = vi.fn();
  const setCookies = vi.fn();
  const openModal = vi.fn();
  const setPage = vi.fn();
  function Harness() {
    const [search, setSearch] = useState("");
    return (
      <PoolPage
        metrics={undefined}
        accounts={options.accounts ?? accounts}
        pool={
          options.pool === false
            ? undefined
            : {
                strategy: "round_robin",
                max_concurrency: 2,
                cooldown: 30,
                acquire_timeout: 60,
              }
        }
        search={search}
        setSearch={setSearch}
        busy={options.busy ?? false}
        act={async (action) => {
          await action();
        }}
        api={api}
        ask={ask}
        setEdit={setEdit}
        setLabel={setLabel}
        setCookies={setCookies}
        openModal={openModal}
        setPage={setPage}
      />
    );
  }
  render(<Harness />);
  return {
    user: userEvent.setup(),
    request,
    ask,
    setEdit,
    setLabel,
    setCookies,
    openModal,
    setPage,
  };
}
afterEach(cleanup);
describe("账号池", () => {
  it("按有效状态筛选、保留 busy、支持状态回退及名称/ID 搜索", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "可用 2" }));
    expect(screen.getByText("账号 alpha")).toBeTruthy();
    expect(screen.getByText("账号 fallback")).toBeTruthy();
    expect(screen.queryByText("账号 beta")).toBeNull();
    await user.click(screen.getByRole("button", { name: "需关注 3" }));
    expect(screen.getByText("账号 beta")).toBeTruthy();
    expect(screen.getByText("账号 delta")).toBeTruthy();
    expect(screen.queryByText("账号 gamma")).toBeNull();
    await user.type(screen.getByLabelText("搜索账号"), " BETA ");
    expect(screen.getByText("账号 beta")).toBeTruthy();
    expect(screen.queryByText("账号 delta")).toBeNull();
    await user.click(screen.getByRole("button", { name: "全部 6" }));
    await user.clear(screen.getByLabelText("搜索账号"));
    expect(screen.getByText("账号 gamma")).toBeTruthy();
  });
  it("搜索/筛选无结果有明确提示并可清除，不误显示首次接入", async () => {
    const { user } = setup();
    await user.type(screen.getByLabelText("搜索账号"), "missing");
    expect(screen.getByRole("status").textContent).toContain("没有匹配的账号");
    expect(screen.queryByText("暂无账号")).toBeNull();
    await user.click(screen.getByRole("button", { name: "清除筛选" }));
    expect((screen.getByLabelText("搜索账号") as HTMLInputElement).value).toBe(
      "",
    );
    expect(screen.getByText("账号 alpha")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "需关注 3" }));
    await user.type(screen.getByLabelText("搜索账号"), "alpha");
    expect(screen.getByText("没有匹配的账号")).toBeTruthy();
  });
  it("所有行操作仍走原始契约，删除必须经确认，策略和接入说明可操作", async () => {
    const { user, request, ask, setEdit, setLabel, setCookies, setPage } =
      setup({ accounts: [accounts[0]] });
    const row = within(screen.getAllByRole("row")[1]);
    await user.click(row.getByRole("button", { name: "编辑" }));
    expect(setEdit).toHaveBeenCalledWith(accounts[0]);
    expect(setLabel).toHaveBeenCalledWith(accounts[0].label);
    expect(setCookies).toHaveBeenCalledWith("");
    await user.click(row.getByRole("button", { name: "停用" }));
    expect(request).toHaveBeenCalledWith("/accounts/alpha", "PATCH", {
      enabled: false,
    });
    await user.click(row.getByRole("button", { name: "保活" }));
    expect(request).toHaveBeenCalledWith("/accounts/alpha/renew", "POST");
    await user.click(row.getByRole("button", { name: "重置" }));
    expect(request).toHaveBeenCalledWith("/accounts/alpha/reset", "POST");
    await user.click(row.getByRole("button", { name: "删除" }));
    expect(ask).toHaveBeenCalledWith(
      expect.stringContaining("此操作不可撤销"),
      expect.any(Function),
    );
    expect(request).not.toHaveBeenCalledWith("/accounts/alpha", "DELETE");
    await ask.mock.calls[0][1]();
    expect(request).toHaveBeenCalledWith("/accounts/alpha", "DELETE");
    await user.selectOptions(screen.getByLabelText("调度策略"), "affinity");
    expect(request).toHaveBeenCalledWith("/pool", "PATCH", {
      strategy: "affinity",
    });
    await user.click(screen.getByText("接入说明"));
    await user.click(screen.getByRole("button", { name: "管理 API 密钥 →" }));
    expect(setPage).toHaveBeenCalledWith(4);
  });
  it("busy 与未同步时保留禁用语义，空池支持首次登录", async () => {
    const { user, request } = setup({ accounts: [accounts[0]], busy: true });
    for (const name of ["停用", "保活", "重置"]) {
      const button = screen.getByRole("button", { name }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      await user.click(button);
    }
    expect(
      (screen.getByLabelText("调度策略") as HTMLSelectElement).disabled,
    ).toBe(true);
    expect(request).not.toHaveBeenCalled();
    cleanup();
    const empty = setup({ accounts: [], pool: false });
    expect(
      (screen.getByLabelText("调度策略") as HTMLSelectElement).disabled,
    ).toBe(true);
    await empty.user.click(
      screen.getByRole("button", { name: "登录上游账号" }),
    );
    expect(empty.openModal).toHaveBeenCalledWith("login");
  });
});
