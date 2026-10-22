import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Account } from "../api";
import { AccountIssue } from "./AccountIssue";

const base: Account = {
  id: "example",
  label: "演示账号",
  status: "active",
  enabled: true,
  inflight: 0,
  effective_max_concurrency: 1,
  success_count: 5,
  fail_count: 1,
  last_used_at: null,
};

afterEach(cleanup);

describe("账号问题提示", () => {
  it.each([
    ["composer_ready_timeout", "输入框未就绪，发送超时"],
    ["upstream_timeout", "等待上游响应超时"],
    ["upstream_auth_failed", "登录验证未通过"],
    ["upstream_quota_exhausted", "上游额度不足"],
    ["media_missing", "只收到文字，未取得媒体"],
    ["upstream_refused", "上游未返回所需结果"],
    ["upstream_error", "上游服务异常"],
    ["request_failed", "请求失败（未记录具体原因）"],
  ])("把 %s 显示为灰色历史原因", (code, label) => {
    const { container } = render(
      <AccountIssue account={{ ...base, last_error_code: code }} />,
    );
    expect(screen.getByText(`上次请求：${label}`)).toBeTruthy();
    expect(container.querySelector("details")?.open).toBe(false);
    expect(container.querySelector(".account-reauth, .danger-text")).toBeNull();
  });

  it("可用账号的历史鉴权错误不等于当前失效，展开查看时间和说明", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AccountIssue
        account={{
          ...base,
          last_error_code: "upstream_auth_failed",
          last_error_at: 1700000000,
        }}
      />,
    );
    await user.click(screen.getByText("上次请求：登录验证未通过"));
    expect(container.querySelector("details")?.open).toBe(true);
    expect(screen.getByText(/发生于/)).toBeTruthy();
    expect(
      screen.getByText("仅为上次请求记录，当前可用性看状态列。"),
    ).toBeTruthy();
    expect(container.querySelector(".account-reauth")).toBeNull();
  });

  it("账号实际失效才显示当前重新登录提示，停用不掩盖失效", () => {
    const { container } = render(
      <AccountIssue
        account={{ ...base, status: "invalid", effective_status: "disabled" }}
      />,
    );
    expect(screen.getByText("登录已失效，请重新登录")).toBeTruthy();
    expect(container.querySelector(".account-reauth")).toBeTruthy();
    expect(container.querySelector("details")).toBeNull();
  });

  it("旧记录和未知代码不透传错误或伪造时间", () => {
    const { container } = render(
      <AccountIssue
        account={{
          ...base,
          last_error: "cookie=secret",
          last_error_code: "token=secret",
        }}
      />,
    );
    expect(
      screen.getByText("上次请求：请求失败（未记录具体原因）"),
    ).toBeTruthy();
    expect(container.textContent).not.toContain("secret");
    expect(container.textContent).not.toContain("发生于");
  });

  it("成功清空错误后不留提示", () => {
    const { container, rerender } = render(
      <AccountIssue
        account={{ ...base, last_error_code: "upstream_timeout" }}
      />,
    );
    rerender(<AccountIssue account={base} />);
    expect(container.textContent).toBe("");
  });
});
