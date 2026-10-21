import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { setImmediate as realImmediate } from "node:timers";
import { Engine } from "../src/engine";
import { collect } from "../src/collector";
import { NAMES, originOf, request } from "../src/policy";
const SECRET = "COOKIE_SECRET_SENTINEL";
const TOKEN = "DEVICE_TOKEN_SENTINEL";
const partition = {
  topLevelSite: "https://example.org",
  hasCrossSiteAncestor: true,
};
let local: Record<string, any>, session: Record<string, any>, api: any;
function cookie(name: string, storeId = "0", p?: typeof partition) {
  return {
    name,
    value: SECRET,
    domain: ".muse.ai",
    path: "/",
    secure: true,
    httpOnly: true,
    sameSite: "lax",
    hostOnly: false,
    session: true,
    storeId,
    ...(p ? { partitionKey: p } : {}),
  };
}
function storage(data: Record<string, any>) {
  return {
    get: vi.fn(async (key: string) => structuredClone({ [key]: data[key] })),
    set: vi.fn(async (value: any) => {
      Object.assign(data, structuredClone(value));
    }),
    setAccessLevel: vi.fn(async () => {}),
  };
}
function paired(e: Engine) {
  e.meta = {
    enabled: true,
    state: "paired",
    sources: {},
    pair: {
      origin: "https://receiver.test",
      development: false,
      device_id: "device",
      server_id: "server",
      token: TOKEN,
      expires_at: Date.now() / 1000 + 3600,
    },
  };
}
function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  local = {};
  session = {};
  api = {
    storage: { local: storage(local), session: storage(session) },
    permissions: {
      contains: vi.fn(async () => true),
      request: vi.fn(async () => true),
    },
    tabs: {
      query: vi.fn(async () => [
        { id: 10, incognito: false },
        { id: 99, incognito: true },
      ]),
    },
    webNavigation: {
      getAllFrames: vi.fn(async () => [
        { url: "https://muse.ai/chat", frameId: 4 },
        { url: "https://accounts.google.com", frameId: 5 },
      ]),
    },
    cookies: {
      getAllCookieStores: vi.fn(async () => [
        { id: "0", tabIds: [10] },
        { id: "private", tabIds: [99] },
      ]),
      getPartitionKey: vi.fn(async () => ({ partitionKey: partition })),
      getAll: vi.fn(async (q: any) =>
        q.partitionKey ? [] : [cookie(q.name, q.storeId)],
      ),
    },
    alarms: { create: vi.fn(async () => {}), clear: vi.fn(async () => true) },
  };
  vi.stubGlobal("chrome", api);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, options: any) => {
      const body = JSON.parse(options.body);
      return response({
        id: body.capture_id,
        status: "committed",
        credential_version: 1,
        code: "ok",
      });
    }),
  );
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});
describe("transport and trust boundary", () => {
  it("accepts only exact safe origins and explicit loopback development", () => {
    expect(originOf("https://receiver.test/")).toBe("https://receiver.test");
    for (const value of [
      "http://receiver.test",
      "http://127.0.0.1",
      "https://x.test/path",
      "https://u:p@x.test",
      "https://x.test/?q=1",
      "file:///",
    ])
      expect(() => originOf(value)).toThrow();
    expect(originOf("http://127.0.0.1:18610", true)).toBe(
      "http://127.0.0.1:18610",
    );
    expect(() => originOf("http://192.168.1.2", true)).toThrow();
  });
  it("disables redirects, ambient cookies, caching and referrers", async () => {
    vi.mocked(fetch).mockResolvedValue(response({}));
    await request("https://receiver.test", "/credential-ingestions", {}, TOKEN);
    expect(fetch).toHaveBeenCalledWith(
      "https://receiver.test/credential-ingestions",
      expect.objectContaining({
        redirect: "error",
        credentials: "omit",
        cache: "no-store",
        referrerPolicy: "no-referrer",
      }),
    );
  });
  it("requires granted permission and both server identities to match", async () => {
    const e = new Engine();
    await e.init();
    api.permissions.contains.mockResolvedValueOnce(false);
    await expect(e.inspect("https://receiver.test", false)).rejects.toThrow(
      "permission_required",
    );
    vi.mocked(fetch).mockResolvedValue(
      response({ server_id: "a", fingerprint: "fp" }),
    );
    await e.inspect("https://receiver.test", false);
    vi.mocked(fetch).mockResolvedValue(
      response({ server_id: "b", fingerprint: "fp" }),
    );
    await expect(e.pair("challenge")).rejects.toThrow("server_changed");
    expect(e.meta.pair).toBeUndefined();
    vi.mocked(fetch)
      .mockResolvedValueOnce(response({ server_id: "a", fingerprint: "fp" }))
      .mockResolvedValueOnce(
        response({
          server_id: "b",
          token: TOKEN,
          device_id: "d",
          expires_at: Date.now() / 1000 + 100,
        }),
      );
    await expect(e.pair("challenge")).rejects.toThrow("invalid_pairing");
  });
  it("stores device token only in trusted local; never stores challenge", async () => {
    const e = new Engine();
    await e.init();
    vi.mocked(fetch)
      .mockResolvedValueOnce(response({ server_id: "a", fingerprint: "fp" }))
      .mockResolvedValueOnce(response({ server_id: "a", fingerprint: "fp" }))
      .mockResolvedValueOnce(
        response({
          server_id: "a",
          token: TOKEN,
          device_id: "d",
          expires_at: Date.now() / 1000 + 100,
        }),
      );
    await e.inspect("https://receiver.test", false);
    await e.pair("ONETIME_CHALLENGE");
    expect(local.meta.pair.token).toBe(TOKEN);
    expect(local.meta.enabled).toBe(false);
    expect(JSON.stringify({ local, session })).not.toContain(
      "ONETIME_CHALLENGE",
    );
    expect(api.storage.local.setAccessLevel).toHaveBeenCalledWith({
      accessLevel: "TRUSTED_CONTEXTS",
    });
    expect(api.storage.session.setAccessLevel).toHaveBeenCalledWith({
      accessLevel: "TRUSTED_CONTEXTS",
    });
  });
});
describe("read-only collection", () => {
  it("skips empty actual partitions instead of duplicating the public ready source", async () => {
    const s = await collect();
    expect(s).toHaveLength(1);
    expect(s.map((x) => x.partition)).toEqual([null]);
    expect(s[0].state).toBe("ready");
    expect(s[0].cookies[0]).toMatchObject({ httpOnly: true, sameSite: "lax" });
    expect(s[0].cookies.every((c) => c.partitionKey === undefined)).toBe(true);
    expect(api.cookies.getPartitionKey).toHaveBeenCalledExactlyOnceWith({
      tabId: 10,
      frameId: 4,
    });
    for (const [q] of api.cookies.getAll.mock.calls) {
      expect(q.domain).toBe("muse.ai");
      expect(NAMES).toContain(q.name);
      expect(q.storeId).toBe("0");
    }
    api.tabs.query.mockResolvedValue([
      { id: 10, incognito: false },
      { id: 11, incognito: false },
    ]);
    api.cookies.getAllCookieStores.mockResolvedValue([
      { id: "0", tabIds: [10] },
      { id: "1", tabIds: [11] },
    ]);
    expect(new Set((await collect()).map((x) => x.key)).size).toBe(2);
  });
  it("rejects ambiguity, expiry, unrelated domains and unexpected partition/store", async () => {
    api.cookies.getAll.mockImplementation(async (q: any) => [
      cookie(q.name),
      { ...cookie(q.name), path: "/other" },
    ]);
    expect((await collect())[0].state).toBe("ambiguous");
    api.cookies.getAll.mockImplementation(async (q: any) => [
      { ...cookie(q.name), domain: ".google.com" },
      cookie(q.name, "private"),
      { ...cookie(q.name), session: false, expirationDate: 1 },
      cookie(q.name, "0", { ...partition, hasCrossSiteAncestor: false }),
    ]);
    expect(
      (await collect()).every(
        (s) => s.state === "awaiting_login" && s.cookies.length === 0,
      ),
    ).toBe(true);
  });
  it("treats a session without hatch_gw as ready, matching current muse.ai cookies", async () => {
    api.cookies.getPartitionKey.mockResolvedValue({} as any);
    api.cookies.getAll.mockImplementation(async (q: any) =>
      q.name === "hatch_sess" || q.name === "hatch_vml"
        ? [cookie(q.name, q.storeId, q.partitionKey)]
        : [],
    );
    const snapshots = await collect();
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].state).toBe("ready");
    expect(snapshots[0].cookies.map((c) => c.name)).toEqual([
      "hatch_sess",
      "hatch_vml",
    ]);
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    expect(fetch).toHaveBeenCalledTimes(1);
    const envelope = JSON.parse(
      vi.mocked(fetch).mock.calls[0][1]!.body as string,
    );
    expect(envelope.cookies.map((c: any) => c.name).sort()).toEqual([
      "hatch_sess",
      "hatch_vml",
    ]);
  });
  it("normalizes session expiry to null and preserves persistent expiry and unspecified sameSite", async () => {
    const expires = Date.now() / 1000 + 3600;
    api.cookies.getAll.mockImplementation(async (q: any) => [
      {
        ...cookie(q.name, q.storeId, q.partitionKey),
        expirationDate: expires,
        session: q.name !== "hatch_gw",
        sameSite: "unspecified",
      },
    ]);
    for (const s of await collect())
      for (const c of s.cookies) {
        expect(c.sameSite).toBe("unspecified");
        expect(c.expires).toBe(c.session ? null : expires);
        expect(JSON.parse(JSON.stringify(c))).toHaveProperty(
          "expires",
          c.session ? null : expires,
        );
      }
  });
  it("uploads a mixed unpartitioned session and single actual partition without fabricating cookie partition keys", async () => {
    api.cookies.getAll.mockImplementation(async (q: any) => {
      const partitioned = q.name === "hatch_vml";
      return partitioned === !!q.partitionKey
        ? [cookie(q.name, q.storeId, q.partitionKey)]
        : [];
    });
    const snapshots = await collect();
    expect(snapshots.map((s) => s.state)).toEqual(["ready", "ready"]);
    const mixed = snapshots[1];
    expect(mixed.partition).toEqual(partition);
    expect(mixed.cookies).toHaveLength(4);
    expect(
      mixed.cookies.find((c) => c.name === "hatch_vml")?.partitionKey,
    ).toEqual(partition);
    for (const c of mixed.cookies.filter((c) => c.name !== "hatch_vml"))
      expect(c).not.toHaveProperty("partitionKey");
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    const envelopes = vi
      .mocked(fetch)
      .mock.calls.map((call) => JSON.parse(call[1]!.body as string));
    const envelope = envelopes.find((item) => item.partition_key)!;
    expect(envelope.partition_key).toEqual(partition);
    expect(
      envelope.cookies.find((c: any) => c.name === "hatch_sess"),
    ).not.toHaveProperty("partitionKey");
    expect(
      envelope.cookies.find((c: any) => c.name === "hatch_vml").partitionKey,
    ).toEqual(partition);
  });
  it("keeps same-name conflicts ambiguous even when values match across public and partitioned cookies", async () => {
    api.cookies.getAll.mockImplementation(async (q: any) => [
      cookie(q.name, q.storeId, q.partitionKey),
    ]);
    const snapshots = await collect();
    expect(snapshots[0].state).toBe("ready");
    expect(snapshots[1].state).toBe("ambiguous");
    expect(
      snapshots[1].cookies.filter((c) => c.name === "hatch_sess"),
    ).toHaveLength(2);
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(
      JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)
        .partition_key,
    ).toBeNull();
  });
  it("never merges two actual partitions or shares public cookies across stores", async () => {
    const other = { ...partition, topLevelSite: "https://other.example" };
    api.tabs.query.mockResolvedValue([
      { id: 10, incognito: false },
      { id: 11, incognito: false },
    ]);
    api.cookies.getAllCookieStores.mockResolvedValue([
      { id: "0", tabIds: [10] },
      { id: "1", tabIds: [11] },
    ]);
    api.webNavigation.getAllFrames.mockResolvedValue([
      { url: "https://muse.ai", frameId: 4 },
      { url: "https://muse.ai", frameId: 6 },
    ]);
    api.cookies.getPartitionKey.mockImplementation(async (q: any) => ({
      partitionKey: q.frameId === 4 ? partition : other,
    }));
    api.cookies.getAll.mockImplementation(async (q: any) => {
      if (!q.partitionKey)
        return q.storeId === "0" && q.name === "hatch_gw"
          ? [cookie(q.name, q.storeId)]
          : [];
      const name =
        q.partitionKey.topLevelSite === partition.topLevelSite
          ? "hatch_vml"
          : "hatch_native_auth_device";
      return q.name === name ? [cookie(q.name, q.storeId, q.partitionKey)] : [];
    });
    const snapshots = await collect();
    expect(snapshots).toHaveLength(6);
    expect(snapshots.every((s) => s.state === "awaiting_login")).toBe(true);
    for (const s of snapshots) {
      expect(
        s.cookies
          .filter((c) => c.partitionKey)
          .every(
            (c) =>
              JSON.stringify(c.partitionKey) === JSON.stringify(s.partition),
          ),
      ).toBe(true);
      if (s.store === "1")
        expect(s.cookies.every((c) => c.partitionKey !== undefined)).toBe(true);
    }
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not fabricate absent partition metadata", async () => {
    api.cookies.getPartitionKey.mockResolvedValue({
      partitionKey: { topLevelSite: partition.topLevelSite },
    });
    await expect(collect()).rejects.toThrow("partition_unsupported");
  });
});
describe("queue lifecycle and privacy", () => {
  it("deduplicates snapshots and never exposes cookie/token through status or persistent cookie storage", async () => {
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    expect(fetch).toHaveBeenCalledTimes(1);
    await e.capture();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(local)).not.toContain(SECRET);
    expect(JSON.stringify(e.status())).not.toContain(SECRET);
    expect(JSON.stringify(e.status())).not.toContain(TOKEN);
    expect(e.meta.sources[Object.keys(e.meta.sources)[0]].version).toBe(1);
  });
  it("bounds offline retries across worker restarts, retaining capture id and base version", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error(`network ${SECRET}`));
    let e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    const ids = Object.values(e.session.jobs).map((j) => j.envelope.capture_id);
    expect(JSON.stringify(session)).toContain(SECRET);
    expect(JSON.stringify(local)).not.toContain(SECRET);
    expect(JSON.stringify(e.status())).not.toContain(SECRET);
    for (let i = 0; i < 4; i++) {
      vi.advanceTimersByTime(300_000);
      e = new Engine();
      await e.init();
      await e.capture();
      await e.drain();
    }
    expect(fetch).toHaveBeenCalledTimes(5);
    expect(Object.keys(e.session.jobs)).toHaveLength(0);
    expect(Object.values(e.session.states)).toEqual(["retry_exhausted"]);
    const bodies = vi
      .mocked(fetch)
      .mock.calls.map(([, o]) => JSON.parse(o!.body as string));
    expect(new Set(bodies.map((b) => b.capture_id))).toEqual(new Set(ids));
    expect(bodies.every((b) => b.base_credential_version === 0)).toBe(true);
  });
  it("recaptures after browser restart without persistent cookie secrets", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    const old = Object.values(e.session.jobs)[0].envelope;
    session.session = undefined;
    const restarted = new Engine();
    await restarted.init();
    await restarted.capture();
    const fresh = Object.values(restarted.session.jobs)[0].envelope;
    expect(fresh.source_revision).toBeGreaterThan(old.source_revision);
    expect(fresh.capture_id).not.toBe(old.capture_id);
    expect(fresh.base_credential_version).toBe(old.base_credential_version);
  });
  it("blocks conflicts without modifying base or replaying snapshot, including on restart", async () => {
    vi.mocked(fetch).mockImplementation(async (_u, o) =>
      response({
        id: JSON.parse(o!.body as string).capture_id,
        status: "conflict",
        credential_version: 999,
        code: SECRET,
      }),
    );
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    await e.capture(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(
      Object.values(e.meta.sources).every((s) => s.blocked && s.version === 0),
    ).toBe(true);
    expect(JSON.stringify(e.status())).not.toContain(SECRET);
    session.session = undefined;
    const restart = new Engine();
    await restart.init();
    await restart.capture();
    expect(fetch).toHaveBeenCalledTimes(1);
    await restart.confirm();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(restart.status().sources[0].state).toBe(
      "candidate_lost_new_capture_requires_confirmation",
    );
    await restart.recaptureLost();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.every(
          ([, o]) =>
            JSON.parse(o!.body as string).base_credential_version === 0,
        ),
    ).toBe(true);
  });
  it("keeps the original blocked conflict despite naturally changed cookies", async () => {
    vi.mocked(fetch).mockImplementation(async (_u, o) =>
      response({
        id: JSON.parse(o!.body as string).capture_id,
        status: "conflict",
        code: "conflict",
      }),
    );
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    await e.capture(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    api.cookies.getAll.mockImplementation(async (q: any) =>
      q.partitionKey
        ? []
        : [{ ...cookie(q.name, q.storeId), value: "ROTATED" }],
    );
    await e.capture(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(e.status().sources[0].state).toBe("conflict");
    expect(e.status().sources[0].capture_id).toBe(
      JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string).capture_id,
    );
  });
  it("does not retry identity confirmation even after cookie changes or browser restart", async () => {
    vi.mocked(fetch).mockImplementation(async (_u, o) =>
      response({
        id: JSON.parse(o!.body as string).capture_id,
        status: "needs_identity_confirmation",
        code: "confirm",
      }),
    );
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    expect(Object.values(e.session.jobs).every((j) => j.blocked)).toBe(true);
    api.cookies.getAll.mockImplementation(async (q: any) =>
      q.partitionKey
        ? []
        : [{ ...cookie(q.name, q.storeId), value: "CHANGED" }],
    );
    vi.advanceTimersByTime(300_000);
    await e.capture(true);
    await e.drain();
    expect(fetch).toHaveBeenCalledTimes(1);
    session.session = undefined;
    const restarted = new Engine();
    await restarted.init();
    await restarted.capture(true);
    await restarted.drain();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(
      Object.values(restarted.meta.sources).every(
        (s) => s.version === 0 && s.identityConfirmation,
      ),
    ).toBe(true);
    expect(
      restarted
        .status()
        .sources.every(
          (s) => s.state === "candidate_lost_new_capture_requires_confirmation",
        ),
    ).toBe(true);
    const original = vi
      .mocked(fetch)
      .mock.calls.map(([, o]) => JSON.parse(o!.body as string).capture_id);
    await restarted.confirm();
    expect(fetch).toHaveBeenCalledTimes(1);
    await restarted.recaptureLost();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.slice(1)
        .every(
          ([, o]) =>
            !original.includes(JSON.parse(o!.body as string).capture_id),
        ),
    ).toBe(true);
    expect(Object.values(restarted.session.jobs).every((j) => j.blocked)).toBe(
      true,
    );
  });
  it("reposts retry_wait with the identical capture and envelope until committed", async () => {
    const seen = new Set<string>();
    vi.mocked(fetch).mockImplementation(async (_url, o) => {
      const body = JSON.parse(o!.body as string);
      const first = !seen.has(body.capture_id);
      seen.add(body.capture_id);
      return response({
        id: body.capture_id,
        status: first ? "retry_wait" : "committed",
        credential_version: 3,
        code: "ok",
      });
    });
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    vi.advanceTimersByTime(60_000);
    await e.drain();
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls).toHaveLength(2);
    expect(
      calls.every(
        ([u, o]) =>
          u === "https://receiver.test/credential-ingestions" &&
          o!.method === "POST",
      ),
    ).toBe(true);
    expect(calls[1][1]!.body).toBe(calls[0][1]!.body);
    expect(e.session.jobs).toEqual({});
    expect(Object.values(e.meta.sources).every((s) => s.version === 3)).toBe(
      true,
    );
  });
  it.each(["conflict", "needs_identity_confirmation"])(
    "preserves blocked %s through worker restart and reposts the original capture after admin confirmation",
    async (status) => {
      let approved = false;
      vi.mocked(fetch).mockImplementation(async (_url, o) => {
        const body = JSON.parse(o!.body as string);
        return response({
          id: body.capture_id,
          status: approved ? "committed" : status,
          account_id: "account-1",
          credential_version: 7,
          code: "ok",
        });
      });
      const e = new Engine();
      await e.init();
      paired(e);
      await e.capture();
      const originals = vi.mocked(fetch).mock.calls.map(([, o]) => o!.body);
      expect(
        e
          .status()
          .sources.every(
            (s) =>
              s.capture_id &&
              s.account_id === "account-1" &&
              s.credential_version === 7 &&
              s.base_credential_version === 0,
          ),
      ).toBe(true);
      expect(JSON.stringify(e.status())).not.toContain(SECRET);
      expect(JSON.stringify(local)).not.toContain(SECRET);
      api.cookies.getAll.mockResolvedValue([]);
      const restarted = new Engine();
      await restarted.init();
      await restarted.capture(true);
      await restarted.drain();
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(
        Object.values(restarted.session.jobs).every((j) => j.blocked),
      ).toBe(true);
      approved = true;
      await restarted.confirm();
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(
        vi
          .mocked(fetch)
          .mock.calls.slice(1)
          .map(([, o]) => o!.body),
      ).toEqual(originals);
      expect(restarted.session.jobs).toEqual({});
      expect(
        Object.values(restarted.meta.sources).every(
          (s) => s.version === 7 && !s.blocked,
        ),
      ).toBe(true);
    },
  );
  it("pauses on auth denial and clears pending secrets on pause/forget", async () => {
    vi.mocked(fetch).mockResolvedValue(response({}, 401));
    const e = new Engine();
    await e.init();
    paired(e);
    await e.capture();
    expect(e.meta.enabled).toBe(false);
    expect(e.session.jobs).toEqual({});
    paired(e);
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    await e.capture();
    await e.enable(false);
    expect(JSON.stringify(session)).not.toContain(SECRET);
    await e.forget();
    expect(JSON.stringify(local)).not.toContain(TOKEN);
  });
});
it("worker debounces cookie bursts, ignores unrelated cookies, and rejects webpage senders", async () => {
  const listeners: Record<string, Function> = {};
  api.cookies.onChanged = {
    addListener: (fn: Function) => {
      listeners.cookie = fn;
    },
  };
  api.webNavigation.onCompleted = {
    addListener: (fn: Function) => {
      listeners.navigation = fn;
    },
  };
  api.alarms.onAlarm = {
    addListener: (fn: Function) => {
      listeners.alarm = fn;
    },
  };
  api.runtime = {
    id: "extension-id",
    getURL: (p: string) => `chrome-extension://extension-id/${p}`,
    onMessage: {
      addListener: (fn: Function) => {
        listeners.message = fn;
      },
    },
  };
  const e = new Engine();
  paired(e);
  await e.save();
  await import("../src/worker");
  const flush = async () => {
    for (let i = 0; i < 100; i++)
      await new Promise((resolve) => realImmediate(resolve));
  };
  await flush();
  expect(fetch).toHaveBeenCalledTimes(1);
  api.cookies.getAll.mockClear();
  listeners.cookie({
    cookie: { ...cookie("hatch_sess"), domain: ".google.com" },
  });
  await flush();
  expect(api.cookies.getAll).not.toHaveBeenCalled();
  for (let i = 0; i < 12; i++)
    listeners.cookie({ cookie: cookie("hatch_sess") });
  await flush();
  expect(api.cookies.getAll).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(801);
  await flush();
  expect(api.cookies.getAll).toHaveBeenCalledTimes(8);
  const respond = vi.fn();
  expect(
    listeners.message(
      { type: "enable", enabled: true },
      { id: "extension-id", url: "https://muse.ai", tab: { id: 1 } },
      respond,
    ),
  ).toBe(false);
  expect(
    listeners.message(
      { type: "status" },
      { id: "extension-id", url: "chrome-extension://extension-id/popup.html" },
      respond,
    ),
  ).toBe(true);
  await flush();
  expect(JSON.stringify(respond.mock.calls)).not.toContain(SECRET);
  expect(JSON.stringify(respond.mock.calls)).not.toContain(TOKEN);
});
it("accepts options page opened as a tab and rejects foreign senders", async () => {
  const listeners: Record<string, Function> = {};
  api.cookies.onChanged = {
    addListener: (fn: Function) => {
      listeners.cookie = fn;
    },
  };
  api.webNavigation.onCompleted = {
    addListener: (fn: Function) => {
      listeners.navigation = fn;
    },
  };
  api.alarms.onAlarm = {
    addListener: (fn: Function) => {
      listeners.alarm = fn;
    },
  };
  api.runtime = {
    id: "extension-id",
    getURL: (p: string) => `chrome-extension://extension-id/${p}`,
    onMessage: {
      addListener: (fn: Function) => {
        listeners.message = fn;
      },
    },
  };
  const e = new Engine();
  await e.init();
  await import("../src/worker");
  const respond = vi.fn();
  expect(
    listeners.message(
      { type: "status" },
      {
        id: "extension-id",
        url: "chrome-extension://extension-id/options.html",
        tab: { id: 7 },
      },
      respond,
    ),
  ).toBe(true);
  for (let i = 0; i < 50 && respond.mock.calls.length === 0; i++)
    await new Promise((resolve) => realImmediate(resolve));
  expect(respond.mock.calls[0][0].ok).toBe(true);
  const rejected = vi.fn();
  expect(
    listeners.message(
      { type: "status" },
      { id: "extension-id", url: "https://muse.ai/" },
      rejected,
    ),
  ).toBe(false);
  expect(
    listeners.message(
      { type: "status" },
      { id: "other-id", url: "chrome-extension://extension-id/options.html" },
      rejected,
    ),
  ).toBe(false);
  expect(rejected).not.toHaveBeenCalled();
});
it("rotates only the bound device token without returning it to the UI", async () => {
  const e = new Engine();
  await e.init();
  paired(e);
  vi.mocked(fetch).mockResolvedValue(response({ token: "NEW_DEVICE_TOKEN" }));
  const status = await e.rotate();
  expect(local.meta.pair.token).toBe("NEW_DEVICE_TOKEN");
  expect(JSON.stringify(status)).not.toContain("NEW_DEVICE_TOKEN");
  expect(fetch).toHaveBeenCalledWith(
    "https://receiver.test/credential-ingestions/rotate",
    expect.objectContaining({
      headers: expect.objectContaining({ Authorization: `Bearer ${TOKEN}` }),
    }),
  );
});
it("persisted final attempt cannot be retried after termination during I/O", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  const e = new Engine();
  await e.init();
  paired(e);
  await e.capture();
  for (const j of Object.values(e.session.jobs)) {
    j.attempts = 5;
    j.due = 0;
  }
  await e.save();
  const restart = new Engine();
  await restart.init();
  await restart.drain();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(restart.session.jobs).toEqual({});
});
it("static safety: no cookie writes/external messages/scripts/dangerous permissions/logging", () => {
  const manifest = JSON.parse(readFileSync("manifest.json", "utf8"));
  expect(manifest.permissions).toEqual([
    "cookies",
    "storage",
    "alarms",
    "webNavigation",
  ]);
  expect(manifest.host_permissions).toEqual(["https://muse.ai/*"]);
  expect(manifest.externally_connectable).toBeUndefined();
  expect(manifest.content_scripts).toBeUndefined();
  const source = readdirSync("src")
    .filter((f) => f.endsWith(".ts"))
    .map((f) => readFileSync(`src/${f}`, "utf8"))
    .join("\n");
  expect(source).not.toMatch(
    /cookies\.(set|remove)\s*\(|onMessageExternal|console\.|storage\.sync|localStorage|debugger\./,
  );
  expect(source.match(/permissions\.request\(/g)).toHaveLength(1);
});
