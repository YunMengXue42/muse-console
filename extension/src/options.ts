import { originOf, permission } from "./policy";
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const status = document.getElementById("status")!;
async function send(type: string, args = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...args });
  if (!response?.ok) throw new Error("操作失败，请检查权限与服务器身份。");
  return response.data;
}
function bind(id: string, fn: () => Promise<unknown>) {
  document.getElementById(id)!.addEventListener("click", () => {
    void fn().then(
      (data) => {
        status.textContent = JSON.stringify(data, null, 2);
      },
      () => {
        status.textContent = "操作失败，请检查权限、身份、配对或网络。";
      },
    );
  });
}
function invalidate() {
  input("pair").disabled = true;
  input("confirmIdentity").checked = false;
  document.getElementById("identity")!.textContent = "目标已变，请重新查询身份";
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
  document.getElementById("identity")!.textContent = JSON.stringify(
    identity,
    null,
    2,
  );
  input("pair").disabled = false;
  return "请在受信控制台核对身份";
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
    status.textContent = JSON.stringify(data, null, 2);
  },
  () => {
    status.textContent = "无法读取状态";
  },
);
