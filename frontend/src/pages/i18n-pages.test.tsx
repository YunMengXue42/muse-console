import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Api, type Account } from "../api";
import { setLang } from "../i18n";
import { TasksPage } from "./TasksPage";
import { AccountIssue } from "./AccountIssue";

afterEach(() => {
  cleanup();
  setLang("zh-CN");
});

it("renders the tasks empty state in English and Chinese", () => {
  const props = {
    tasks: [],
    busy: false,
    setDetail: vi.fn(),
    ask: vi.fn(),
    api: new Api("test"),
  };
  setLang("en");
  const { rerender } = render(<TasksPage {...props} />);
  expect(screen.getByRole("heading", { name: "Recent tasks" })).toBeTruthy();
  expect(screen.getByText("No tasks yet")).toBeTruthy();
  setLang("zh-CN");
  rerender(<TasksPage {...props} />);
  expect(screen.getByRole("heading", { name: "最近任务" })).toBeTruthy();
  expect(screen.getByText("暂无任务")).toBeTruthy();
});

const account: Account = {
  id: "example",
  label: "Example",
  status: "active",
  enabled: true,
  inflight: 0,
  effective_max_concurrency: 1,
  success_count: 0,
  fail_count: 1,
  last_used_at: null,
};

it("translates backend reasons by code and preserves unknown legacy text in English", () => {
  setLang("en");
  const { rerender } = render(
    <AccountIssue
      account={{ ...account, last_error_code: "upstream_timeout" }}
    />,
  );
  expect(
    screen.getByText(
      "Last request: The last request timed out waiting for the upstream response. Try again later.",
    ),
  ).toBeTruthy();
  rerender(
    <AccountIssue
      account={{
        ...account,
        last_error: "上次请求等待上游响应超时，请稍后重试",
      }}
    />,
  );
  expect(
    screen.getByText(
      "Last request: The last request timed out waiting for the upstream response. Try again later.",
    ),
  ).toBeTruthy();
  rerender(
    <AccountIssue account={{ ...account, last_error: "Legacy reason" }} />,
  );
  expect(screen.getByText("Last request: Legacy reason")).toBeTruthy();
});
