import { useSyncExternalStore } from "react";
import messages from "./messages.json";

export type Locale = "zh-CN" | "en";
export const LOCALE_STORAGE_KEY = "ainative.locale";
const listeners = new Set<() => void>();
const catalog: Record<string, Record<Locale, string>> = messages;

function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved === "zh-CN" || saved === "en") return saved;
  } catch {
    /* Storage can be disabled in private or embedded browsers. */
  }
  return (navigator.languages?.[0] ?? navigator.language)
    .toLowerCase()
    .startsWith("zh")
    ? "zh-CN"
    : "en";
}
let locale: Locale = detectLocale();
export function getLocale(): Locale {
  return locale;
}

function updateDocument() {
  document.documentElement.lang = locale;
  document.title = t("AI-Native Data Platform");
}
export function setLocale(next: Locale) {
  locale = next;
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, next);
  } catch {
    /* Use an in-memory preference. */
  }
  updateDocument();
  listeners.forEach((listener) => listener());
}
window.addEventListener("storage", (event) => {
  if (event.key !== LOCALE_STORAGE_KEY && event.key !== null) return;
  locale = detectLocale();
  updateDocument();
  listeners.forEach((listener) => listener());
});
export function useLocale(): Locale {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, getLocale);
}

/** Interpolate once so placeholder-like user values are never interpreted. */
export function t(
  key: string,
  values: Record<string, string | number> = {},
): string {
  return (catalog[key]?.[locale] ?? key).replace(
    /\{(\w+)\}/g,
    (token, name: string) =>
      values[name] === undefined ? token : String(values[name]),
  );
}
export function formatDate(value: string, timeOnly = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return timeOnly
    ? date.toLocaleTimeString(locale)
    : date.toLocaleString(locale);
}
export function formatNumber(
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(locale, options).format(value);
}
updateDocument();

export interface Message {
  key: string;
  values: Record<string, string | number>;
}
export function message(
  key: string,
  values: Record<string, string | number> = {},
): Message {
  return { key, values };
}
export function translateMessage(value: Message): string {
  return t(value.key, value.values);
}

/** Localized guidance; the original server detail remains available separately. */
export function errorMessage(error: {
  code?: string;
  message?: string;
}): string {
  if (error.message && catalog[error.message]) return t(error.message);
  const key = `error.${error.code}`;
  return catalog[key] ? t(key) : (error.message ?? t("Failed"));
}
