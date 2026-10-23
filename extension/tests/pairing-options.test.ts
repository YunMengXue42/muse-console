import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
// Reuse the already locked frontend test dependency; no extension dependency added.
import { JSDOM } from "../../frontend/node_modules/jsdom/lib/api.js";
import {
  parsePairingPackage,
  matchesPairingIdentity,
} from "../src/pairing-package";
const identity = {
  origin: "https://receiver.test",
  server_id: "server",
  fingerprint: "fp",
};
const pack = (overrides = {}) =>
  JSON.stringify({
    schema: "muse-console-pairing",
    version: 1,
    ...identity,
    challenge: "ONE_TIME_SYNTHETIC",
    expires_at: Date.now() / 1000 + 300,
    ...overrides,
  });
let dom: JSDOM;
let api: any;
const field = (id: string) => document.getElementById(id) as HTMLInputElement;
const flush = async () => {
  for (let i = 0; i < 15; i++) await Promise.resolve();
};
async function load(raw = pack()) {
  field("pairingPackage").value = raw;
  field("pairingPackage").dispatchEvent(new dom.window.Event("input"));
  field("loadPackage").click();
  await flush();
}
beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  dom = new JSDOM(readFileSync("options.html", "utf8"), {
    url: "https://extension.test/options.html",
  });
  vi.stubGlobal("document", dom.window.document);
  vi.stubGlobal("window", dom.window);
  api = {
    i18n: { getMessage: (k: string) => k, getUILanguage: () => "en" },
    permissions: { request: vi.fn(async () => true) },
    storage: { local: { set: vi.fn() }, session: { set: vi.fn() } },
    runtime: {
      sendMessage: vi.fn(async (m: any) => ({
        ok: true,
        data:
          m.type === "inspect"
            ? identity
            : {
                paired: m.type === "pair",
                enabled: false,
                state: "paired_sync_disabled",
                sources: [],
              },
      })),
    },
  };
  vi.stubGlobal("chrome", api);
  await import("../src/options");
  await flush();
});
afterEach(() => {
  dom.window.dispatchEvent(new dom.window.Event("pagehide"));
  dom.window.close();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("strictly validates size, schema, fields, origins, types, expiry and identity", () => {
  expect(parsePairingPackage(pack())).toMatchObject(identity);
  expect(matchesPairingIdentity(parsePairingPackage(pack()), identity)).toBe(
    true,
  );
  expect(
    matchesPairingIdentity(parsePairingPackage(pack()), {
      ...identity,
      fingerprint: "wrong",
    }),
  ).toBe(false);
  for (const origin of [
    "http://receiver.test",
    "https://receiver.test/path",
    "https://u:p@receiver.test",
    "https://receiver.test?x=1",
    "https://receiver.test#x",
    "https://receiver.test/",
    "https://*.test",
    "file:///",
  ]) {
    expect(() => parsePairingPackage(pack({ origin }))).toThrow();
  }
  for (const bad of [
    { schema: "wrong" },
    { version: "1" },
    { server_id: 1 },
    { fingerprint: "" },
    { challenge: "x".repeat(4097) },
    { extra: true },
    { expires_at: 1 },
    { expires_at: "9999999999" },
    { expires_at: Date.now() / 1000 + 901 },
  ])
    expect(() => parsePairingPackage(pack(bad))).toThrow();
  expect(() => parsePairingPackage(" ".repeat(8193))).toThrow();
  expect(() =>
    parsePairingPackage(pack({ origin: "http://127.0.0.1:18610" })),
  ).toThrow();
  expect(
    parsePairingPackage(pack({ origin: "http://127.0.0.1:18610" }), true)
      .origin,
  ).toContain("127.0.0.1");
});
it("prefills only, requests exact permission on click, requires identity confirmation and separate sync consent", async () => {
  const ids = Array.from(
    document.querySelectorAll("[id]"),
    (element) => element.id,
  );
  expect(new Set(ids).size).toBe(ids.length);
  await load();
  expect(field("origin").value).toBe(identity.origin);
  expect(field("challenge").value).toBe("ONE_TIME_SYNTHETIC");
  expect(field("pairingPackage").value).toBe("");
  expect(api.permissions.request).not.toHaveBeenCalled();
  expect(api.runtime.sendMessage.mock.calls.map(([m]: any) => m.type)).toEqual([
    "status",
  ]);
  expect(field("confirmIdentity").checked).toBe(false);
  expect(field("consent").checked).toBe(false);
  field("inspect").click();
  await flush();
  expect(api.permissions.request).toHaveBeenCalledWith({
    origins: ["https://receiver.test/*"],
  });
  expect(field("pair").disabled).toBe(false);
  field("pair").click();
  await flush();
  expect(
    api.runtime.sendMessage.mock.calls.some(([m]: any) => m.type === "pair"),
  ).toBe(false);
  field("confirmIdentity").checked = true;
  field("pair").click();
  await flush();
  const message = api.runtime.sendMessage.mock.calls.find(
    ([m]: any) => m.type === "pair",
  )[0];
  expect(JSON.parse(message.pairingPackage).challenge).toBe(
    "ONE_TIME_SYNTHETIC",
  );
  expect(field("challenge").value).toBe("");
  field("enable").click();
  await flush();
  expect(
    api.runtime.sendMessage.mock.calls.some(([m]: any) => m.type === "enable"),
  ).toBe(false);
  expect(api.storage.local.set).not.toHaveBeenCalled();
  expect(api.storage.session.set).not.toHaveBeenCalled();
});
it.each(["server_id", "fingerprint", "origin"])(
  "refuses a mismatching %s and clears secrets",
  async (key) => {
    await load();
    api.runtime.sendMessage.mockResolvedValueOnce({
      ok: true,
      data: { ...identity, [key]: "wrong" },
    });
    field("inspect").click();
    await flush();
    expect(field("pair").disabled).toBe(true);
    // Invalid origin responses are rejected without displaying arbitrary data.
    expect(document.getElementById("identity")!.textContent).not.toContain(
      "wrong",
    );
    if (key !== "origin") expect(field("challenge").value).toBe("");
  },
);
it("clears expired packages, target edits and closes; rejects invalid pasted data without permission", async () => {
  await load(pack({ expires_at: 1 }));
  expect(field("challenge").value).toBe("");
  expect(api.permissions.request).not.toHaveBeenCalled();
  await load(pack({ expires_at: Date.now() / 1000 + 1 }));
  await vi.advanceTimersByTimeAsync(1001);
  expect(field("challenge").value).toBe("");
  expect(field("pair").disabled).toBe(true);
  await load();
  field("origin").value = "https://other.test";
  field("origin").dispatchEvent(new dom.window.Event("input"));
  expect(field("challenge").value).toBe("");
  await load();
  dom.window.dispatchEvent(new dom.window.Event("pagehide"));
  expect(field("challenge").value).toBe("");
});
it("does not accept a stale inspection after the target changes", async () => {
  await load();
  let resolve: (x: unknown) => void = () => {};
  api.runtime.sendMessage.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  field("inspect").click();
  await flush();
  field("origin").dispatchEvent(new dom.window.Event("input"));
  resolve({ ok: true, data: identity });
  await flush();
  expect(field("pair").disabled).toBe(true);
});
