import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import App from "./App";
import { setLang, t } from "./i18n";

afterEach(() => {
  cleanup();
  setLang("zh-CN");
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("console internationalization", () => {
  it("renders the login screen in English and switches back to Chinese", () => {
    sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({}))),
    );
    setLang("en");
    render(<App />);
    expect(screen.getByLabelText("Admin key")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enter console" })).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "English" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    act(() => setLang("zh-CN"));
    expect(screen.getByLabelText("管理员 Key")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByLabelText("Admin key")).toBeTruthy();
    expect(document.documentElement.lang).toBe("en");
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(screen.getByLabelText("管理员 Key")).toBeTruthy();
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("falls back to Chinese for unknown keys and fills placeholders", () => {
    setLang("en");
    expect(t("未收录的消息")).toBe("未收录的消息");
    expect(t("未收录：{name}，共 {n} 个", { name: "Muse", n: 3 })).toBe(
      "未收录：Muse，共 3 个",
    );
    expect(t("请求失败（{status}）", { status: 503 })).toBe(
      "Request failed (503)",
    );
    setLang("zh-CN");
    expect(t("请求失败（{status}）", { status: 503 })).toBe("请求失败（503）");
  });
});

describe("English plurals", () => {
  it("chooses singular or plural by count", () => {
    setLang("en");
    try {
      expect(t("{n} 个账号", { n: 1 })).toBe("1 account");
      expect(t("{n} 个账号", { n: 3 })).toBe("3 accounts");
      expect(
        t("显示 {visible} / {total} 个账号", { visible: 1, total: 1 }),
      ).toBe("Showing 1 / 1 account");
    } finally {
      setLang("zh-CN");
    }
  });
});
