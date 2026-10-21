import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Api } from "../api";
import { KeysPage } from "./KeysPage";

function page() {
  render(
    <KeysPage
      keys={[]}
      builtin={[]}
      setKeyEdit={vi.fn()}
      setLabel={vi.fn()}
      ask={vi.fn()}
      api={new Api("test-only")}
      openModal={vi.fn()}
    />,
  );
}

afterEach(cleanup);

describe("调用地址", () => {
  it("显示当前来源的 v1 地址并只复制地址", async () => {
    const user = userEvent.setup();
    const write = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    page();
    const input = screen.getByRole("textbox", {
      name: "调用地址",
    }) as HTMLInputElement;
    expect(input.value).toBe(`${window.location.origin}/v1`);
    expect(input.readOnly).toBe(true);
    await user.click(screen.getByRole("button", { name: "复制地址" }));
    expect(write).toHaveBeenCalledWith(`${window.location.origin}/v1`);
    expect(screen.getByRole("button", { name: "已复制" })).toBeTruthy();
  });

  it("剪贴板拒绝时保留可手动复制的地址", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
      new Error("denied"),
    );
    page();
    await user.click(screen.getByRole("button", { name: "复制地址" }));
    expect(screen.getByRole("status").textContent).toBe(
      "复制失败，请选中地址复制",
    );
    expect(screen.getByRole("button", { name: "复制地址" })).toBeTruthy();
  });
});
