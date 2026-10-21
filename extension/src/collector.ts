import {
  allowedCookie,
  museURL,
  NAMES,
  partitionOf,
  REQUIRED_NAMES,
  sourceKey,
  type Envelope,
  type Partition,
} from "./policy";
export interface Snapshot {
  key: string;
  store: string;
  partition: Partition | null;
  cookies: Envelope["cookies"];
  state: "ready" | "awaiting_login" | "ambiguous";
}
// No page scripts, debugger, cookie writes, or cross-site cookie queries.
export async function collect(): Promise<Snapshot[]> {
  const tabs = (await chrome.tabs.query({})).filter(
    (t) => !t.incognito && t.id !== undefined,
  );
  const normal = new Set(tabs.map((t) => t.id!));
  const stores = await chrome.cookies.getAllCookieStores();
  const output: Snapshot[] = [];
  for (const store of stores) {
    const ids = store.tabIds.filter((id) => normal.has(id));
    if (!ids.length) continue;
    const partitions = new Map<string, Partition | null>([["null", null]]);
    for (const tabId of ids) {
      // webNavigation is required to discover actual Muse subframes (including cross-site ancestors).
      const frames = await chrome.webNavigation.getAllFrames({ tabId });
      for (const frame of frames ?? []) {
        if (!museURL(frame.url)) continue;
        const result = await chrome.cookies.getPartitionKey({
          tabId,
          frameId: frame.frameId,
        });
        const p = partitionOf(result.partitionKey);
        if (p) partitions.set(JSON.stringify(p), p);
      }
    }
    // The null group is queried first; only its cookies may be shared within this store.
    let unpartitioned: chrome.cookies.Cookie[] = [];
    for (const partition of partitions.values()) {
      const found: chrome.cookies.Cookie[] = partition
        ? [...unpartitioned]
        : [];
      for (const name of NAMES) {
        const rows = await chrome.cookies.getAll({
          domain: "muse.ai",
          name,
          storeId: store.id,
          ...(partition ? { partitionKey: partition } : {}),
        });
        for (const c of rows) {
          if (!allowedCookie(c) || c.storeId !== store.id) continue;
          const actual = partitionOf(c.partitionKey);
          if (JSON.stringify(actual) !== JSON.stringify(partition)) continue;
          if (
            !c.session &&
            (!c.expirationDate || c.expirationDate <= Date.now() / 1000)
          )
            continue;
          found.push(c);
        }
      }
      if (partition && found.length === unpartitioned.length) continue;
      if (!partition) unpartitioned = [...found];
      const ambiguous = NAMES.some(
        (name) => found.filter((c) => c.name === name).length > 1,
      );
      const cookies: Envelope["cookies"] = found.map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        secure: c.secure,
        httpOnly: c.httpOnly,
        sameSite: c.sameSite,
        hostOnly: c.hostOnly,
        session: c.session,
        expires: c.session ? null : c.expirationDate!,
        ...(c.partitionKey
          ? { partitionKey: partitionOf(c.partitionKey)! }
          : {}),
      }));
      output.push({
        key: sourceKey(store.id, partition),
        store: store.id,
        partition,
        cookies,
        state: ambiguous
          ? "ambiguous"
          : REQUIRED_NAMES.every((name) =>
                found.some((c) => c.name === name && c.value),
              )
            ? "ready"
            : "awaiting_login",
      });
    }
  }
  return output;
}
