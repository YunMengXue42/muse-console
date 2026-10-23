import { parsePairingPackage, matchesPairingIdentity } from "./pairing-package";
import { collect } from "./collector";
import {
  originOf,
  permission,
  request,
  type Envelope,
  type Pairing,
  type Result,
} from "./policy";
interface Source {
  revision: number;
  version: number;
  blocked?: boolean;
  identityConfirmation?: boolean;
}
interface Meta {
  pair?: Pairing;
  enabled: boolean;
  sources: Record<string, Source>;
  state: string;
}
interface Job {
  envelope: Envelope;
  attempts: number;
  due: number;
  blocked?: boolean;
  account_id?: string;
  credential_version?: number;
}
interface Session {
  jobs: Record<string, Job>;
  fingerprints: Record<string, string>;
  states: Record<string, string>;
  inspected?: {
    origin: string;
    development: boolean;
    server_id: string;
    fingerprint: string;
  };
}
const emptySession = (): Session => ({
  jobs: {},
  fingerprints: {},
  states: {},
});
export class Engine {
  meta: Meta = { enabled: false, sources: {}, state: "not_paired" };
  session: Session = emptySession();
  async init() {
    await chrome.storage.local.setAccessLevel({
      accessLevel: "TRUSTED_CONTEXTS",
    });
    await chrome.storage.session.setAccessLevel({
      accessLevel: "TRUSTED_CONTEXTS",
    });
    this.meta = (await chrome.storage.local.get("meta")).meta ?? this.meta;
    this.session =
      (await chrome.storage.session.get("session")).session ?? emptySession();
    if (this.meta.pair)
      originOf(this.meta.pair.origin, this.meta.pair.development);
  }
  async save() {
    await chrome.storage.local.set({ meta: this.meta });
    await chrome.storage.session.set({ session: this.session });
  }
  status() {
    return {
      paired: !!this.meta.pair,
      origin: this.meta.pair?.origin,
      enabled: this.meta.enabled,
      state: this.meta.state,
      sources: Object.keys({
        ...this.meta.sources,
        ...this.session.states,
      }).map((source) => {
        const job = this.session.jobs[source];
        return {
          source,
          state:
            this.meta.sources[source]?.blocked && !job
              ? "candidate_lost_new_capture_requires_confirmation"
              : this.session.states[source],
          ...(job
            ? {
                capture_id: job.envelope.capture_id,
                account_id: job.account_id,
                credential_version: job.credential_version,
                base_credential_version: job.envelope.base_credential_version,
              }
            : {}),
        };
      }),
      blocked: Object.values(this.meta.sources).some((s) => s.blocked),
    };
  }
  async inspect(input: string, development: boolean) {
    delete this.session.inspected;
    const origin = originOf(input, development);
    if (!(await chrome.permissions.contains({ origins: [permission(origin)] })))
      throw new Error("permission_required");
    const data = (await request(origin, "/credential-ingestions/server")) as {
      server_id?: string;
      fingerprint?: string;
    };
    if (
      typeof data.server_id !== "string" ||
      !data.server_id ||
      typeof data.fingerprint !== "string" ||
      !data.fingerprint
    )
      throw new Error("invalid_server");
    this.session.inspected = {
      origin,
      development,
      server_id: data.server_id,
      fingerprint: data.fingerprint,
    };
    await this.save();
    return { server_id: data.server_id, fingerprint: data.fingerprint, origin };
  }
  async pair(challenge: string, pairingPackage?: string) {
    const target = this.session.inspected;
    if (!target || !challenge || challenge.length > 4096)
      throw new Error("inspect_first");
    const expected =
      pairingPackage === undefined
        ? undefined
        : parsePairingPackage(pairingPackage, target.development);
    if (
      expected &&
      (!matchesPairingIdentity(expected, target) ||
        expected.challenge !== challenge)
    )
      throw new Error("server_changed");
    // Recheck the displayed identity immediately before exchanging the one-time challenge.
    const identity = (await request(
      target.origin,
      "/credential-ingestions/server",
    )) as { server_id: string; fingerprint: string };
    if (
      identity.server_id !== target.server_id ||
      identity.fingerprint !== target.fingerprint
    )
      throw new Error("server_changed");
    if (expected) parsePairingPackage(pairingPackage!, target.development);
    const p = (await request(target.origin, "/credential-ingestions/pair", {
      challenge,
      server_id: target.server_id,
    })) as Pairing;
    if (
      p.server_id !== target.server_id ||
      typeof p.token !== "string" ||
      !p.token ||
      typeof p.device_id !== "string" ||
      !p.device_id ||
      !Number.isFinite(p.expires_at) ||
      p.expires_at <= Date.now() / 1000
    )
      throw new Error("invalid_pairing");
    this.meta = {
      pair: {
        origin: target.origin,
        development: target.development,
        device_id: p.device_id,
        token: p.token,
        server_id: p.server_id,
        expires_at: p.expires_at,
      },
      enabled: false,
      sources: {},
      state: "paired_sync_disabled",
    };
    this.session = emptySession();
    await this.save();
    return this.status();
  }
  async enable(enabled: boolean) {
    if (!this.meta.pair) throw new Error("pair_first");
    this.meta.enabled = enabled;
    if (!enabled) {
      this.session.jobs = {};
      this.meta.state = "paused";
    }
    await this.save();
    if (enabled) await this.capture();
    return this.status();
  }
  async rotate() {
    const p = this.meta.pair;
    if (!p) throw new Error("pair_first");
    const result = (await request(
      p.origin,
      "/credential-ingestions/rotate",
      {},
      p.token,
    )) as { token?: string; expires_at?: number };
    if (typeof result.token !== "string" || !result.token)
      throw new Error("invalid_rotation");
    p.token = result.token;
    if (
      Number.isFinite(result.expires_at) &&
      result.expires_at! > Date.now() / 1000
    )
      p.expires_at = result.expires_at!;
    await this.save();
    return this.status();
  }
  async forget() {
    this.meta = { enabled: false, sources: {}, state: "not_paired" };
    this.session = emptySession();
    await this.save();
    return this.status();
  }
  async confirm() {
    // The administrator approves a capture, not a freshly collected replacement.
    for (const [key, source] of Object.entries(this.meta.sources)) {
      const job = this.session.jobs[key];
      if (source.blocked && job?.blocked) {
        job.blocked = false;
        job.attempts = 0;
        job.due = Date.now();
      }
    }
    await this.save();
    await this.drain();
    return this.status();
  }
  async recaptureLost() {
    // Explicit new-candidate action, never reuse an approval for a lost capture.
    for (const [key, source] of Object.entries(this.meta.sources)) {
      if (source.blocked && !this.session.jobs[key]) {
        source.blocked = false;
        source.identityConfirmation = false;
        delete this.session.fingerprints[key];
      }
    }
    await this.save();
    await this.capture();
    return this.status();
  }
  async capture(cookieEvent = false) {
    if (!this.meta.enabled || !this.meta.pair) return;
    const snapshots = await collect();
    const present = new Set(snapshots.map((s) => s.key));
    for (const key of Object.keys(this.session.jobs)) {
      if (!present.has(key) && !this.meta.sources[key]?.blocked)
        delete this.session.jobs[key];
    }
    for (const s of snapshots) {
      const source = (this.meta.sources[s.key] ??= { revision: 0, version: 0 });
      const digest = Array.from(
        new Uint8Array(
          await crypto.subtle.digest(
            "SHA-256",
            new TextEncoder().encode(JSON.stringify(s.cookies)),
          ),
        ),
      )
        .map((n) => n.toString(16).padStart(2, "0"))
        .join("");
      const previous = this.session.fingerprints[s.key];
      if (source.blocked) {
        this.session.states[s.key] = this.session.jobs[s.key]
          ? (this.session.states[s.key] ?? "confirmation_required")
          : "candidate_lost_new_capture_requires_confirmation";
        continue;
      }
      if (s.state !== "ready") {
        delete this.session.jobs[s.key];
        this.session.states[s.key] = s.state;
        this.session.fingerprints[s.key] = digest;
        continue;
      }
      if (previous === digest) continue;
      this.session.fingerprints[s.key] = digest;
      const envelope: Envelope = {
        schema_version: 1,
        source: "extension",
        device_id: this.meta.pair.device_id,
        capture_id: crypto.randomUUID(),
        source_revision: ++source.revision,
        captured_at: Math.floor(Date.now() / 1000),
        store_id: s.store,
        partition_key: s.partition,
        base_credential_version: source.version,
        cookies: s.cookies,
      };
      this.session.jobs[s.key] = { envelope, attempts: 0, due: Date.now() };
      this.session.states[s.key] = "collected";
    }
    await this.save();
    await this.drain();
  }
  async drain() {
    const p = this.meta.pair;
    if (!this.meta.enabled || !p) return;
    if (p.expires_at <= Date.now() / 1000) {
      this.meta.enabled = false;
      this.meta.state = "device_expired";
      this.session.jobs = {};
      await this.save();
      return;
    }
    for (const [key, job] of Object.entries(this.session.jobs)) {
      if (job.blocked) continue;
      if (job.attempts >= 5) {
        this.session.states[key] = "retry_exhausted";
        delete this.session.jobs[key];
        await this.save();
        continue;
      }
      if (job.due > Date.now()) continue;
      // Persist attempt BEFORE I/O: worker termination cannot reset the retry budget.
      job.attempts++;
      job.due =
        Date.now() + Math.min(30_000 * 2 ** (job.attempts - 1), 300_000);
      await this.save();
      try {
        const r = (await request(
          p.origin,
          "/credential-ingestions",
          job.envelope,
          p.token,
        )) as Result;
        if (
          typeof r.id !== "string" ||
          !r.id ||
          ![
            "committed",
            "awaiting_login",
            "rejected",
            "retry_wait",
            "needs_identity_confirmation",
            "conflict",
          ].includes(r.status)
        )
          throw new Error("request_rejected");
        this.session.states[key] = r.status; // Never expose untrusted server code/error text.
        if (r.status === "retry_wait") {
          // A receipt lookup cannot restart synchronous validation: retry this exact POST.
        } else {
          if (r.status === "committed") {
            if (
              !Number.isSafeInteger(r.credential_version) ||
              r.credential_version! < 0
            )
              throw new Error("request_rejected");
            this.meta.sources[key].version = r.credential_version!;
            this.meta.sources[key].blocked = false;
            this.meta.sources[key].identityConfirmation = false;
          }
          if (
            r.status === "conflict" ||
            r.status === "needs_identity_confirmation"
          ) {
            this.meta.sources[key].blocked = true;
            this.meta.sources[key].identityConfirmation =
              r.status === "needs_identity_confirmation";
            job.blocked = true;
            job.account_id =
              typeof r.account_id === "string" ? r.account_id : undefined;
            job.credential_version =
              Number.isSafeInteger(r.credential_version) &&
              r.credential_version! >= 0
                ? r.credential_version
                : undefined;
          } else delete this.session.jobs[key];
        }
      } catch (e) {
        const code = e instanceof Error ? e.message : "";
        this.session.states[key] = "retry_wait";
        if (code === "device_unauthorized") {
          this.meta.enabled = false;
          this.meta.state = "device_unauthorized";
          this.session.jobs = {};
          break;
        }
        if (code === "request_rejected") {
          this.session.states[key] = "rejected";
          delete this.session.jobs[key];
        }
      }
      if (job.attempts >= 5 && this.session.jobs[key] && !job.blocked) {
        this.session.states[key] = "retry_exhausted";
        delete this.session.jobs[key];
      }
      await this.save();
    }
    await this.save();
    const due = Object.values(this.session.jobs)
      .filter((j) => !j.blocked)
      .map((j) => j.due);
    if (due.length)
      await chrome.alarms.create("retry", {
        when: Math.max(Date.now() + 1000, Math.min(...due)),
      });
    else await chrome.alarms.clear("retry");
  }
}
