import { useSyncExternalStore } from "react";

/** Appearance is a per-device preference, kept in localStorage. */
export type Theme = "light" | "dark";

const STORAGE_KEY = "locus-theme";
const THEME_EVENT = "locus:theme-changed";

export function readTheme(): Theme {
  if (typeof window === "undefined") {
    return "light";
  }
  try {
    return window.localStorage?.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

/** Apply the theme class to <html> (the token aliases in globals.css follow it). */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") {
    return;
  }
  const html = document.documentElement;
  html.classList.remove("theme-light", "theme-dark");
  html.classList.add(`theme-${theme}`);
}

export function setTheme(theme: Theme): void {
  try {
    window.localStorage?.setItem(STORAGE_KEY, theme);
  } catch {
    // Storage can be unavailable (private mode); the class still applies.
  }
  applyTheme(theme);
  window.dispatchEvent(new Event(THEME_EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(THEME_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, readTheme, () => "light");
}
