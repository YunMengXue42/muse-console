/** Chrome's locale selection and substitution rules are the source of truth. */
export function msg(key: string, subs?: string | string[]): string {
  return chrome.i18n.getMessage(key, subs) || key;
}

export function localizePage(root: ParentNode = document): void {
  document.documentElement.lang = chrome.i18n.getUILanguage();
  for (const element of Array.from(
    root.querySelectorAll<HTMLElement>("[data-i18n]"),
  )) {
    element.textContent = msg(element.dataset.i18n!);
  }
  for (const element of Array.from(
    root.querySelectorAll<HTMLElement>("[data-i18n-attr]"),
  )) {
    for (const entry of element.dataset.i18nAttr!.split(";")) {
      const [attribute, key] = entry.split(":");
      if (attribute && key) element.setAttribute(attribute, msg(key));
    }
  }
}
