/** Portable, non-persistent quick pairing format. expires_at is Unix seconds. */
export interface PairingPackage {
  schema: "muse-console-pairing";
  version: 1;
  origin: string;
  server_id: string;
  fingerprint: string;
  challenge: string;
  expires_at: number;
}
export const MAX_PAIRING_BYTES = 8192;
export function parsePairingPackage(
  raw: string,
  development = false,
  now = Date.now(),
): PairingPackage {
  if (
    raw.length > MAX_PAIRING_BYTES ||
    new TextEncoder().encode(raw).length > MAX_PAIRING_BYTES
  )
    throw new Error("invalid_pairing_package");
  const p: unknown = JSON.parse(raw);
  if (!p || typeof p !== "object" || Array.isArray(p))
    throw new Error("invalid_pairing_package");
  const v = p as Record<string, unknown>;
  const keys = [
    "schema",
    "version",
    "origin",
    "server_id",
    "fingerprint",
    "challenge",
    "expires_at",
  ];
  if (
    Object.keys(v).length !== keys.length ||
    keys.some((k) => !Object.hasOwn(v, k)) ||
    v.schema !== "muse-console-pairing" ||
    v.version !== 1
  )
    throw new Error("invalid_pairing_package");
  for (const [key, max] of [
    ["origin", 2048],
    ["server_id", 256],
    ["fingerprint", 512],
    ["challenge", 4096],
  ] as const) {
    const s = v[key];
    if (
      typeof s !== "string" ||
      !s ||
      s.length > max ||
      s.trim() !== s ||
      /[\s\u0000-\u001f\u007f]/u.test(s)
    )
      throw new Error("invalid_pairing_package");
  }
  const u = new URL(v.origin as string);
  const loopback = ["localhost", "127.0.0.1"].includes(u.hostname);
  if (
    u.hostname.includes("*") ||
    u.origin !== v.origin ||
    u.username ||
    u.password ||
    u.pathname !== "/" ||
    u.search ||
    u.hash ||
    (u.protocol !== "https:" &&
      !(development && loopback && u.protocol === "http:"))
  )
    throw new Error("invalid_pairing_origin");
  if (
    typeof v.expires_at !== "number" ||
    !Number.isFinite(v.expires_at) ||
    v.expires_at * 1000 <= now ||
    v.expires_at * 1000 > now + 15 * 60 * 1000
  )
    throw new Error("expired_pairing_package");
  return v as unknown as PairingPackage;
}
export function matchesPairingIdentity(
  p: PairingPackage,
  identity: { origin: string; server_id: string; fingerprint: string },
): boolean {
  return (
    identity.origin === p.origin &&
    identity.server_id === p.server_id &&
    identity.fingerprint === p.fingerprint
  );
}
