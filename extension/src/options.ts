import { msg, localizePage } from "./i18n";
import { originOf, permission } from "./policy";
import { describeStatus, type StatusView } from "./status-view";
import {
  parsePairingPackage,
  matchesPairingIdentity,
  type PairingPackage,
} from "./pairing-package";
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
localizePage();
const status = document.getElementById("status")!;
let quick: PairingPackage | undefined;
let verified:
  { origin: string; server_id: string; fingerprint: string } | undefined;
let revision = 0;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;
async function send(type: string, args = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...args });
  if (!response?.ok)
    throw new Error(msg("operationFailedCheckPermissionsAndServer"));
  return response.data;
}
function show(data: unknown): string {
  if (typeof data === "string") return data;
  if (data && typeof data === "object" && "paired" in data)
    return describeStatus(data as StatusView);
  return msg("operationCompleted");
}
function bind(id: string, fn: () => Promise<unknown>) {
  document.getElementById(id)!.addEventListener("click", () => {
    void fn().then(
      (data) => {
        status.textContent = show(data);
      },
      () => {
        status.textContent = msg(
          "operationFailedCheckPermissionsIdentityPairing",
        );
      },
    );
  });
}
function invalidate() {
  revision++;
  verified = undefined;
  input("pair").disabled = true;
  input("confirmIdentity").checked = false;
  input("consent").checked = false;
  document.getElementById("identity")!.textContent = msg(
    "targetChangedCheckTheIdentityAgain",
  );
}
function clearQuick() {
  clearTimeout(expiryTimer);
  quick = undefined;
  input("pairingPackage").value = "";
  input("challenge").value = "";
  input("challenge").readOnly = false;
  invalidate();
}
function targetChanged() {
  clearQuick();
}
input("origin").addEventListener("input", targetChanged);
input("development").addEventListener("change", targetChanged);
input("pairingPackage").addEventListener("input", () => {
  // Editing a loaded package never silently falls back to its previous identity/challenge.
  if (quick) {
    const raw = input("pairingPackage").value;
    clearQuick();
    input("pairingPackage").value = raw;
  } else invalidate();
});
bind("loadPackage", async () => {
  const raw = input("pairingPackage").value;
  clearQuick();
  try {
    quick = parsePairingPackage(raw, input("development").checked);
  } catch {
    return msg("invalidQuickPairing");
  }
  input("origin").value = quick.origin;
  input("challenge").value = quick.challenge;
  input("challenge").readOnly = true;
  expiryTimer = setTimeout(
    () => {
      clearQuick();
      status.textContent = msg("quickPairingExpired");
    },
    quick.expires_at * 1000 - Date.now(),
  );
  return msg("quickPairingLoaded");
});
bind("clearPackage", async () => {
  clearQuick();
  return msg("quickPairingCleared");
});
bind("inspect", async () => {
  invalidate();
  const current = revision;
  const expected = quick;
  const development = input("development").checked;
  const origin = originOf(input("origin").value.trim(), development);
  if (expected && expected.expires_at * 1000 <= Date.now()) {
    clearQuick();
    return msg("quickPairingExpired");
  }
  // Keep the permission request in the direct user gesture, before messaging.
  const granted = await chrome.permissions.request({
    origins: [permission(origin)],
  });
  if (!granted || current !== revision) throw new Error();
  const identity = await send("inspect", { origin, development });
  if (current !== revision) throw new Error();
  if (
    !identity ||
    identity.origin !== origin ||
    typeof identity.server_id !== "string" ||
    !identity.server_id ||
    typeof identity.fingerprint !== "string" ||
    !identity.fingerprint
  ) {
    clearQuick();
    throw new Error();
  }
  if (
    expected &&
    (expected.expires_at * 1000 <= Date.now() ||
      !matchesPairingIdentity(expected, identity))
  ) {
    clearQuick();
    return msg("quickPairingIdentityMismatch");
  }
  const { origin: target, server_id, fingerprint } = identity;
  verified = { origin: target, server_id, fingerprint };
  document.getElementById("identity")!.textContent = msg("identityReadout", [
    target,
    server_id,
    fingerprint,
  ]);
  input("pair").disabled = false;
  return msg("checkTheIdentityInYourTrusted");
});
bind("pair", async () => {
  if (!verified || !input("confirmIdentity").checked) throw new Error();
  if (
    quick &&
    (quick.expires_at * 1000 <= Date.now() ||
      !matchesPairingIdentity(quick, verified))
  ) {
    clearQuick();
    return msg("quickPairingExpired");
  }
  const challenge = input("challenge").value;
  const pairingPackage = quick ? JSON.stringify(quick) : undefined;
  clearQuick();
  return send("pair", { challenge, pairingPackage });
});
bind("enable", async () => {
  if (!input("consent").checked) throw new Error();
  return send("enable", { enabled: true });
});
bind("pause", () => send("enable", { enabled: false }));
bind("confirm", () => send("confirm"));
bind("recaptureLost", () => send("recaptureLost"));
bind("forget", () => {
  clearQuick();
  return send("forget");
});
bind("rotate", () => send("rotate"));
window.addEventListener("pagehide", clearQuick);
void send("status").then(
  (data) => {
    status.textContent = show(data);
  },
  () => {
    status.textContent = msg("unableToReadStatus");
  },
);
