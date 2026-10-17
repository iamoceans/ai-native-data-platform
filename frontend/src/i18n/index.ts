import { useSyncExternalStore } from "react";
import messages from "./messages.json";

export type Locale = "zh-CN" | "en";
export const LOCALE_STORAGE_KEY = "ainative.locale";
/** Product name; identical in every locale, so it is a constant, not a catalog entry. */
export const PLATFORM_NAME = "Oceanus";
const listeners = new Set<() => void>();
const catalog: Record<string, Record<Locale, string>> = messages;

function browserLocale(): Locale {
  return (navigator.languages?.[0] ?? navigator.language).toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved === "zh-CN" || saved === "en") return saved;
  } catch {
    /* Storage can be disabled in private or embedded browsers. */
  }
  return browserLocale();
}
let locale: Locale = detectLocale();
let accountId: string | null = null;
let timezone: string | undefined;
let preferenceRevision = 0;
export function getLocale(): Locale {
  return locale;
}

function updateDocument() {
  document.documentElement.lang = locale;
  document.title = PLATFORM_NAME;
}
export function setLocale(next: Locale) {
  locale = next;
  try {
    if (!accountId) localStorage.setItem(LOCALE_STORAGE_KEY, next);
  } catch {
    /* Use an in-memory preference. */
  }
  updateDocument();
  listeners.forEach((listener) => listener());
}
window.addEventListener("storage", (event) => {
  if (accountId && event.key === `ainative.preferences.${accountId}`) {
    window.dispatchEvent(new Event("account-preferences-changed"));
    return;
  }
  if (accountId) return;
  if (event.key !== LOCALE_STORAGE_KEY && event.key !== null) return;
  locale = detectLocale();
  updateDocument();
  listeners.forEach((listener) => listener());
});
export function applyAccountPreferences(profile: { id: string; locale?: Locale | null; timezone?: string | null }) {
  accountId = profile.id;
  timezone = profile.timezone || undefined;
  locale = profile.locale || browserLocale();
  preferenceRevision += 1;
  updateDocument();
  listeners.forEach(listener => listener());
}
export function resetAccountPreferences() {
  accountId = null;
  timezone = undefined;
  locale = detectLocale();
  preferenceRevision += 1;
  updateDocument();
  listeners.forEach(listener => listener());
}
export function notifyAccountPreferences(userId: string) {
  try { localStorage.setItem(`ainative.preferences.${userId}`, String(Date.now())); } catch { /* optional cross-tab notification */ }
}
export function useLocale(): Locale {
  useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, () => `${locale}:${preferenceRevision}`);
  return locale;
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
    ? date.toLocaleTimeString(locale, { timeZone: timezone })
    : date.toLocaleString(locale, { timeZone: timezone });
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
