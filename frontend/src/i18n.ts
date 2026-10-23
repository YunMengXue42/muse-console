/**
 * Minimal i18n without dependencies. Source strings are the Chinese UI text; English
 * lives in locales/en/*.ts keyed by that exact Chinese string. Missing entries fall back
 * to Chinese, so an untranslated string is visible rather than blank.
 *
 * Placeholders use {name}: t("共 {n} 个", { n: 3 }). English plurals: {n|item|items}.
 */
import { useSyncExternalStore } from "react";
import { en } from "./locales/en";

export type Lang = "zh-CN" | "en";
export const LANGS: { id: Lang; label: string }[] = [
  { id: "zh-CN", label: "中文" },
  { id: "en", label: "English" },
];
const STORAGE_KEY = "muse.console.lang";

function detect(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "zh-CN" || saved === "en") return saved;
  } catch {
    /* storage unavailable */
  }
  const preferred =
    typeof navigator === "undefined"
      ? []
      : (navigator.languages?.length
          ? navigator.languages
          : [navigator.language]
        ).filter(Boolean);
  return preferred.length && !preferred[0].toLowerCase().startsWith("zh")
    ? "en"
    : "zh-CN";
}

let current: Lang = detect();
const listeners = new Set<() => void>();

export function getLang(): Lang {
  return current;
}

/** BCP 47 tag for Intl / toLocaleString. */
export function locale(): string {
  return current === "en" ? "en-US" : "zh-CN";
}

export function setLang(lang: Lang): void {
  if (lang === current) return;
  current = lang;
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* storage unavailable */
  }
  applyDocument();
  listeners.forEach((fn) => fn());
}

export function subscribeLang(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function applyDocument(): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = current;
}

export function t(
  source: string,
  vars?: Record<string, string | number | undefined | null>,
): string {
  let text = current === "en" ? (en[source] ?? source) : source;
  if (vars) {
    // English plurals: "{n} {n|request|requests}" picks by vars.n === 1.
    text = text.replace(
      /\{(\w+)\|([^|{}]*)\|([^|{}]*)\}/g,
      (match, name: string, one: string, other: string) =>
        name in vars ? (Number(vars[name]) === 1 ? one : other) : match,
    );
    text = text.replace(/\{(\w+)\}/g, (match, name: string) =>
      name in vars ? String(vars[name] ?? "") : match,
    );
  }
  return text;
}

/** Re-render the calling component when the language changes. Use once near the root. */
export function useLang(): Lang {
  return useSyncExternalStore(subscribeLang, getLang, getLang);
}
