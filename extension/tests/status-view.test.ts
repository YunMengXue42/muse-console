import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import zh from "../_locales/zh_CN/messages.json";
import en from "../_locales/en/messages.json";
import { msg } from "../src/i18n";

function useMessages(messages: Record<string, { message: string }>) {
  vi.stubGlobal("chrome", {
    i18n: {
      getMessage(key: string, subs: string | string[] = []) {
        const values = typeof subs === "string" ? [subs] : subs;
        return (messages[key]?.message ?? "").replace(
          /\$(\d+)/g,
          (_, n) => values[Number(n) - 1] ?? "",
        );
      },
    },
  });
}
beforeEach(() => useMessages(zh));
afterEach(() => vi.unstubAllGlobals());
import { existsSync, readFileSync } from "node:fs";
import { describeStatus, headline } from "../src/status-view";

const source = JSON.stringify(["0", null]);

describe("status view", () => {
  it("shows unpaired state without details", () => {
    expect(headline({ paired: false })).toEqual({
      text: "未配对",
      tone: "off",
    });
    expect(describeStatus({ paired: false, enabled: false, sources: [] })).toBe(
      "设备：未配对\n自动同步：关闭\n会话：暂无",
    );
  });
  it("labels a synced default store and never shows stale disabled state", () => {
    const status = {
      paired: true,
      origin: "https://receiver.test",
      enabled: true,
      state: "paired_sync_disabled",
      sources: [{ source, state: "committed" }],
      blocked: false,
    };
    expect(headline(status).tone).toBe("ok");
    const text = describeStatus(status);
    expect(text).toContain("设备：已配对");
    expect(text).toContain("服务器：https://receiver.test");
    expect(text).toContain("默认窗口：已同步");
  });
  it("surfaces admin confirmation and partitions", () => {
    const status = {
      paired: true,
      enabled: true,
      state: "paired",
      blocked: true,
      sources: [
        {
          source: JSON.stringify([
            "1",
            { topLevelSite: "https://example.org", hasCrossSiteAncestor: true },
          ]),
          state: "needs_identity_confirmation",
          capture_id: "cap-1",
          account_id: "acc-1",
          credential_version: 3,
        },
      ],
    };
    expect(headline(status)).toEqual({ text: "等待管理员确认", tone: "warn" });
    const text = describeStatus(status);
    expect(text).toContain("Cookie 存储 1（分区 https://example.org）");
    expect(text).toContain("候选 cap-1");
    expect(text).toContain("账号 acc-1 · 版本 3");
  });
  it("warns on revoked or expired devices", () => {
    expect(
      headline({ paired: true, enabled: false, state: "device_unauthorized" })
        .tone,
    ).toBe("warn");
    expect(describeStatus({ paired: true, state: "device_expired" })).toContain(
      "设备授权已过期",
    );
  });
  it("keeps unknown codes visible", () => {
    expect(
      describeStatus({
        paired: true,
        enabled: true,
        sources: [{ source, state: "new_code" }],
      }),
    ).toContain("默认窗口：new_code");
  });
});

it("manifest icons exist", () => {
  const manifest = JSON.parse(readFileSync("manifest.json", "utf8"));
  for (const path of [
    ...Object.values(manifest.icons),
    ...Object.values(manifest.action.default_icon),
  ])
    expect(existsSync(path as string)).toBe(true);
});

it("development loopback excludes IPv6, which extension CSP cannot allow", async () => {
  const { originOf } = await import("../src/policy");
  expect(originOf("http://127.0.0.1:18610", true)).toBe(
    "http://127.0.0.1:18610",
  );
  expect(() => originOf("http://[::1]:18610", true)).toThrow();
  const manifest = JSON.parse(readFileSync("manifest.json", "utf8"));
  expect(JSON.stringify(manifest)).not.toContain("[::1]");
});

it("renders English unpaired and sync headlines", () => {
  useMessages(en);
  expect(describeStatus({ paired: false })).toBe(
    "Device: Not paired\nAutomatic sync: Off\nSessions: none",
  );
  expect(
    headline({
      paired: true,
      enabled: true,
      sources: [{ state: "committed" }],
    }),
  ).toEqual({ text: "Sync healthy", tone: "ok" });
  expect(msg("missingKey")).toBe("missingKey");
});
it("renders English partition and version substitutions", () => {
  useMessages(en);
  const text = describeStatus({
    paired: true,
    enabled: true,
    state: "paired",
    sources: [
      {
        source: JSON.stringify(["1", { topLevelSite: "https://example.org" }]),
        state: "committed",
        capture_id: "cap-1",
        account_id: "acc-1",
        credential_version: 3,
      },
    ],
  });
  expect(text).toContain(
    "Cookie store 1 (partition https://example.org): Synced",
  );
  expect(text).toContain("capture_id cap-1");
  expect(text).toContain("Account acc-1 · credential_version 3");
  expect(text).not.toMatch(/[\u4e00-\u9fff]/);
});
