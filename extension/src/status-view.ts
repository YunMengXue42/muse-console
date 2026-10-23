import { msg } from "./i18n";
// Turns Engine.status() into short readable text. Never renders cookie values or tokens:
// only fields that Engine.status() already exposes are read here.
const DEVICE: Record<string, string> = {
  not_paired: "notPaired",
  paired: "paired",
  paired_sync_disabled: "pairedSyncNotEnabled",
  paused: "syncPaused",
  device_expired: "deviceAuthorizationExpiredPairAgain",
  device_unauthorized: "deviceAuthorizationRevokedPairAgain",
};
const SOURCE: Record<string, string> = {
  awaiting_login: "waitingForMuseLogin",
  ambiguous: "multipleSessionsDetectedSkipped",
  collected: "capturedAwaitingUpload",
  committed: "synced",
  retry_wait: "uploadFailedRetryingLater",
  retry_exhausted: "retriesExhaustedCandidateDiscarded",
  rejected: "rejectedByServer",
  needs_identity_confirmation: "waitingForAdminToConfirmAccount",
  conflict: "versionConflictWaitingForAdminConfirmation",
  confirmation_required: "waitingForAdminConfirmation",
  candidate_lost_new_capture_requires_confirmation:
    "candidateLostRecaptureAndConfirmAgain",
};
interface SourceView {
  source?: string;
  state?: string;
  capture_id?: string;
  account_id?: string;
  credential_version?: number;
}
export interface StatusView {
  paired?: boolean;
  origin?: string;
  enabled?: boolean;
  state?: string;
  sources?: SourceView[];
  blocked?: boolean;
}
export function label(map: Record<string, string>, code?: string): string {
  if (!code) return "—";
  return map[code] ? msg(map[code]) : code;
}
function storeName(source?: string): string {
  try {
    const [store, partition] = JSON.parse(source ?? "") as [
      string,
      { topLevelSite?: string } | null,
    ];
    const base =
      store === "0" ? msg("defaultWindow") : msg("cookieStore", [store]);
    return partition?.topLevelSite
      ? msg("partitionStore", [base, partition.topLevelSite])
      : base;
  } catch {
    return msg("unknownSource");
  }
}
export function describeStatus(status: StatusView | undefined): string {
  if (!status) return msg("unableToReadStatus");
  // Engine keeps the pairing-time state after sync is enabled; do not show a stale "disabled".
  const device = !status.paired
    ? msg("notPaired")
    : status.enabled &&
        (status.state === "paired_sync_disabled" || status.state === "paused")
      ? msg("paired")
      : label(DEVICE, status.state);
  const lines = [msg("deviceLine", [device])];
  if (status.origin) lines.push(msg("serverLine", [status.origin]));
  lines.push(msg("syncLine", [status.enabled ? msg("on") : msg("off")]));
  const sources = status.sources ?? [];
  if (!sources.length) lines.push(msg("sessionsNone"));
  for (const s of sources) {
    lines.push(
      msg("sourceLine", [storeName(s.source), label(SOURCE, s.state)]),
    );
    if (s.capture_id) lines.push(msg("captureLine", [s.capture_id]));
    if (s.account_id)
      lines.push(
        msg("accountLine", [s.account_id]) +
          (s.credential_version === undefined
            ? ""
            : msg("versionSuffix", [String(s.credential_version)])),
      );
  }
  if (status.blocked) lines.push(msg("aCandidateNeedsAdminAttention"));
  return lines.join("\n");
}
export function headline(status: StatusView | undefined): {
  text: string;
  tone: "ok" | "wait" | "warn" | "off";
} {
  if (!status?.paired) return { text: msg("notPaired"), tone: "off" };
  if (
    status.state === "device_expired" ||
    status.state === "device_unauthorized"
  )
    return { text: label(DEVICE, status.state), tone: "warn" };
  if (!status.enabled) return { text: msg("syncNotEnabled"), tone: "off" };
  if (status.blocked)
    return { text: msg("waitingForAdminConfirmation"), tone: "warn" };
  const states = (status.sources ?? []).map((s) => s.state);
  if (
    states.some(
      (s) => s === "retry_wait" || s === "rejected" || s === "retry_exhausted",
    )
  )
    return { text: msg("uploadProblem"), tone: "warn" };
  if (states.includes("committed"))
    return { text: msg("syncHealthy"), tone: "ok" };
  if (states.includes("collected"))
    return { text: msg("syncing"), tone: "wait" };
  return { text: msg("waitingForMuseLogin"), tone: "wait" };
}
