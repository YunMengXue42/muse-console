import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
// Reuse the locked frontend jsdom without adding an extension dependency.
// @ts-expect-error jsdom has no declaration in this project
import { JSDOM } from "../../frontend/node_modules/jsdom/lib/api.js";
import { parsePairingPackage } from "../src/pairing-package";

const pkg = () => ({
  schema: "muse-console-pairing",
  version: 1,
  origin: "https://receiver.test",
  server_id: "server",
  fingerprint: "fp",
  challenge: "ONE_TIME_SENTINEL",
  expires_at: Date.now() / 1000 + 300,
});
let dom: JSDOM;
let send: ReturnType<typeof vi.fn>;
let permissions: ReturnType<typeof vi.fn>;
const field = (id: string) => document.getElementById(id) as HTMLInputElement;
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
async function click(id: string) {
  field(id).click();
  await flush();
}
async function load(value = pkg()) {
  field("pairingPackage").value = JSON.stringify(value);
  field("pairingPackage").dispatchEvent(new dom.window.Event("input"));
  await click("loadPackage");
}
beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  dom = new JSDOM(readFileSync("options.html", "utf8"), {
    url: "https://extension.invalid/options.html",
  });
  vi.stubGlobal("window", dom.window);
  vi.stubGlobal("document", dom.window.document);
  const messages = JSON.parse(
    readFileSync("_locales/en/messages.json", "utf8"),
  );
  send = vi.fn(async (m) => ({
    ok: true,
    data:
      m.type === "inspect"
        ? { origin: m.origin, server_id: "server", fingerprint: "fp" }
        : { paired: m.type === "pair", enabled: false, sources: [] },
  }));
  permissions = vi.fn(async () => true);
  vi.stubGlobal("chrome", {
    runtime: { sendMessage: send },
    permissions: { request: permissions },
    i18n: {
      getUILanguage: () => "en",
      getMessage: (key: string) => messages[key]?.message || key,
    },
  });
  await import("../src/options");
  await flush();
});
afterEach(() => {
  dom.window.dispatchEvent(new dom.window.Event("pagehide"));
  dom.window.close();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("prefills without permission or consent; explicit inspect and confirmation pair without enabling sync or persisting secrets", async () => {
  const storage = vi.spyOn(dom.window.Storage.prototype, "setItem");
  await load();
  expect(field("origin").value).toBe(pkg().origin);
  expect(field("challenge").value).toBe(pkg().challenge);
  expect(field("pairingPackage").value).toBe("");
  expect(permissions).not.toHaveBeenCalled();
  expect(field("confirmIdentity").checked).toBe(false);
  expect(field("consent").checked).toBe(false);
  expect(field("pair").disabled).toBe(true);
  await click("inspect");
  expect(permissions).toHaveBeenCalledWith({
    origins: ["https://receiver.test/*"],
  });
  await click("pair");
  expect(send.mock.calls.some(([m]) => m.type === "pair")).toBe(false);
  field("confirmIdentity").checked = true;
  await click("pair");
  const pair = send.mock.calls.find(([m]) => m.type === "pair")![0];
  expect(pair.challenge).toBe(pkg().challenge);
  expect(JSON.parse(pair.pairingPackage).server_id).toBe("server");
  expect(field("challenge").value).toBe("");
  expect(field("confirmIdentity").checked).toBe(false);
  await click("enable");
  expect(send.mock.calls.some(([m]) => m.type === "enable")).toBe(false);
  expect(storage).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain("ONE_TIME_SENTINEL");
});
it.each(["server_id", "fingerprint", "origin"])(
  "rejects mismatched %s",
  async (key) => {
    await load();
    send.mockImplementation(async (m) => ({
      ok: true,
      data: {
        origin: m.origin,
        server_id: "server",
        fingerprint: "fp",
        [key]: "wrong",
      },
    }));
    await click("inspect");
    expect(field("pair").disabled).toBe(true);
    expect(field("challenge").value).toBe("");
    expect(field("confirmIdentity").checked).toBe(false);
  },
);
it("clears expired packages and invalidates target changes, including in-flight inspection", async () => {
  await load();
  await vi.advanceTimersByTimeAsync(301000);
  expect(field("challenge").value).toBe("");
  expect(field("pair").disabled).toBe(true);
  await load();
  let resolve!: (value: unknown) => void;
  send.mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  field("inspect").click();
  await flush();
  field("origin").value = "https://other.test";
  field("origin").dispatchEvent(new dom.window.Event("input"));
  resolve({
    ok: true,
    data: { origin: pkg().origin, server_id: "server", fingerprint: "fp" },
  });
  await flush();
  expect(field("challenge").value).toBe("");
  expect(field("pair").disabled).toBe(true);
});
it("rejects expired and wrong-origin input before requesting any permissions", async () => {
  for (const origin of [
    "http://receiver.test",
    "https://x.test/path",
    "https://user:pass@x.test",
    "https://x.test?x",
    "https://*.test",
    "file:///",
  ]) {
    await load({ ...pkg(), origin });
    expect(field("challenge").value).toBe("");
  }
  await load({ ...pkg(), expires_at: 1 });
  expect(permissions).not.toHaveBeenCalled();
});
it("denied permission cannot unlock pairing", async () => {
  await load();
  permissions.mockResolvedValue(false);
  await click("inspect");
  expect(field("pair").disabled).toBe(true);
  expect(send.mock.calls.some(([m]) => m.type === "inspect")).toBe(false);
});
it("keeps manual pairing and pause/forget available", async () => {
  field("origin").value = pkg().origin;
  await click("inspect");
  field("challenge").value = "manual";
  field("confirmIdentity").checked = true;
  await click("pair");
  expect(send).toHaveBeenCalledWith({
    type: "pair",
    challenge: "manual",
    pairingPackage: undefined,
  });
  await click("pause");
  await click("forget");
  expect(send).toHaveBeenCalledWith({ type: "enable", enabled: false });
  expect(send).toHaveBeenCalledWith({ type: "forget" });
});
it("strictly validates shape, type, UTF-8 size, canonical origin and short validity", () => {
  const valid = pkg();
  expect(parsePairingPackage(JSON.stringify(valid))).toEqual(valid);
  for (const value of [
    [],
    null,
    { ...valid, version: 2 },
    { ...valid, schema: "other" },
    { ...valid, extra: true },
    { ...valid, challenge: 1 },
    { ...valid, expires_at: String(valid.expires_at) },
    { ...valid, expires_at: Date.now() / 1000 + 901 },
    { ...valid, server_id: "a\nb" },
    { ...valid, origin: "https://receiver.test/" },
    { ...valid, origin: "https://RECEIVER.test" },
    { ...valid, challenge: "密".repeat(3000) },
  ]) {
    expect(() => parsePairingPackage(JSON.stringify(value))).toThrow();
  }
  expect(() => parsePairingPackage(" ".repeat(8193))).toThrow();
  expect(() =>
    parsePairingPackage(
      JSON.stringify({ ...valid, origin: "http://127.0.0.1:18610" }),
    ),
  ).toThrow();
  expect(
    parsePairingPackage(
      JSON.stringify({ ...valid, origin: "http://127.0.0.1:18610" }),
      true,
    ).origin,
  ).toBe("http://127.0.0.1:18610");
});
it("keeps frontend and extension pairing protocol copies identical across directories", () => {
  // Intentional mirrored modules: either side changing alone must fail this gate.
  // Resolve relative to this test, independently of the shell's current directory.
  const frontend = new URL(
    "../../frontend/src/pairing-package.ts",
    import.meta.url,
  );
  const extension = new URL("../src/pairing-package.ts", import.meta.url);
  expect(readFileSync(frontend, "utf8")).toBe(readFileSync(extension, "utf8"));
});
