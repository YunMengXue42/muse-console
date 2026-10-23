import { msg } from "./i18n";
import { Engine } from "./engine";
import { allowedCookie, museURL } from "./policy";
const engine = new Engine();
let chain: Promise<unknown> = engine.init();
function enqueue<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn);
  chain = next.catch(() => undefined);
  return next;
}
// All callbacks are registered synchronously; operations serialize across events and network awaits.
let timer: ReturnType<typeof setTimeout> | undefined;
let cookieDirty = false;
async function schedule(cookie = false) {
  cookieDirty ||= cookie;
  const saved = await chrome.storage.session.get("cookieDirty");
  await chrome.storage.session.set({
    cookieDirty: cookieDirty || saved.cookieDirty === true,
  });
  await chrome.alarms.create("capture", { when: Date.now() + 2000 });
  clearTimeout(timer);
  timer = setTimeout(() => {
    void enqueue(runCapture).catch(() => undefined);
  }, 800);
}
async function runCapture() {
  clearTimeout(timer);
  await chrome.alarms.clear("capture");
  const dirty =
    (await chrome.storage.session.get("cookieDirty")).cookieDirty === true;
  await engine.capture(dirty);
  await chrome.storage.session.set({ cookieDirty: false });
  cookieDirty = false;
}
chrome.cookies.onChanged.addListener((change) => {
  if (allowedCookie(change.cookie))
    void enqueue(() => schedule(true)).catch(() => undefined);
});
chrome.webNavigation.onCompleted.addListener(
  (details) => {
    if (museURL(details.url))
      void enqueue(() => schedule()).catch(() => undefined);
  },
  { url: [{ schemes: ["https"], hostEquals: "muse.ai" }] },
);
chrome.alarms.onAlarm.addListener((alarm) => {
  void enqueue(async () => {
    if (alarm.name === "capture") await runCapture();
    if (alarm.name === "retry") await engine.drain();
  }).catch(() => undefined);
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const allowed = ["options.html", "popup.html"].map((p) =>
    chrome.runtime.getURL(p),
  );
  // options.html 由 chrome.runtime.openOptionsPage 打开时位于普通标签页，
  // 因此 sender.tab 存在属于正常情况；sender.url 已限定为扩展自身页面。
  if (sender.id !== chrome.runtime.id || !allowed.includes(sender.url ?? ""))
    return false;
  void enqueue(async () => {
    if (!message || typeof message.type !== "string") throw new Error();
    switch (message.type) {
      case "status":
        return engine.status();
      case "inspect":
        return engine.inspect(message.origin, message.development === true);
      case "pair":
        return engine.pair(message.challenge);
      case "enable":
        return engine.enable(message.enabled === true);
      case "confirm":
        return engine.confirm();
      case "recaptureLost":
        return engine.recaptureLost();
      case "forget":
        return engine.forget();
      case "rotate":
        return engine.rotate();
      default:
        throw new Error();
    }
  }).then(
    (data) => respond({ ok: true, data }),
    () =>
      respond({
        ok: false,
        error: msg("operationFailedCheckPermissionsServerIdentity"),
      }),
  );
  return true;
});
// Every wake reconciles session-only work, including lost alarms after a browser restart.
void enqueue(async () => {
  if ((await chrome.storage.session.get("cookieDirty")).cookieDirty)
    await runCapture();
  else await engine.capture();
  await engine.drain();
}).catch(() => undefined);
