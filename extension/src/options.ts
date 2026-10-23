import { msg, localizePage } from "./i18n";
import { originOf, permission } from "./policy";
import { describeStatus, type StatusView } from "./status-view";
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
localizePage();
const status = document.getElementById("status")!;
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
  return JSON.stringify(data, null, 2);
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
  input("pair").disabled = true;
  input("confirmIdentity").checked = false;
  document.getElementById("identity")!.textContent = msg(
    "targetChangedCheckTheIdentityAgain",
  );
}
input("origin").addEventListener("input", invalidate);
input("development").addEventListener("change", invalidate);
bind("inspect", async () => {
  invalidate();
  const origin = originOf(
    input("origin").value.trim(),
    input("development").checked,
  );
  // Must stay in this direct user-gesture callback, before any asynchronous messaging.
  const granted = await chrome.permissions.request({
    origins: [permission(origin)],
  });
  if (!granted) throw new Error();
  const identity = await send("inspect", {
    origin,
    development: input("development").checked,
  });
  const {
    origin: target,
    server_id,
    fingerprint,
  } = identity as {
    origin: string;
    server_id: string;
    fingerprint: string;
  };
  document.getElementById("identity")!.textContent = msg("identityReadout", [
    target,
    server_id,
    fingerprint,
  ]);
  input("pair").disabled = false;
  return msg("checkTheIdentityInYourTrusted");
});
bind("pair", async () => {
  if (!input("confirmIdentity").checked) throw new Error();
  const challenge = input("challenge").value;
  input("challenge").value = "";
  return send("pair", { challenge });
});
bind("enable", async () => {
  if (!input("consent").checked) throw new Error();
  return send("enable", { enabled: true });
});
bind("pause", () => send("enable", { enabled: false }));
bind("confirm", () => send("confirm"));
bind("recaptureLost", () => send("recaptureLost"));
bind("forget", () => send("forget"));
bind("rotate", () => send("rotate"));
void send("status").then(
  (data) => {
    status.textContent = show(data);
  },
  () => {
    status.textContent = msg("unableToReadStatus");
  },
);
