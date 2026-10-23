import { describeStatus, headline, type StatusView } from "./status-view";
const status = document.getElementById("status")!;
const badge = document.getElementById("state")!;
function render(data: StatusView | undefined) {
  const head = headline(data);
  badge.textContent = head.text;
  badge.dataset.tone = head.tone;
  status.textContent = describeStatus(data);
}
void chrome.runtime.sendMessage({ type: "status" }).then(
  (response) => {
    if (response?.ok) render(response.data);
    else status.textContent = "无法读取状态";
  },
  () => {
    status.textContent = "无法读取状态";
  },
);
document.getElementById("options")!.addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});
