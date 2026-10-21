export {};
const status = document.getElementById("status")!;
void chrome.runtime.sendMessage({ type: "status" }).then(
  (response) => {
    status.textContent = response?.ok
      ? JSON.stringify(response.data, null, 2)
      : "无法读取状态";
  },
  () => {
    status.textContent = "无法读取状态";
  },
);
document.getElementById("options")!.addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});
