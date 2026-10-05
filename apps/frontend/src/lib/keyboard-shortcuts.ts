"use client";

import { useEffect, useRef } from "react";

export type KeyboardShortcutBinding = {
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  enabled?: boolean;
  metaKey?: boolean;
  preventDefault?: boolean;
  shiftKey?: boolean;
  allowInEditable?: boolean;
  handler: (event: KeyboardEvent) => void;
};

type KeyboardShortcutTarget = Document | Window | null | undefined;

type UseKeyboardShortcutsOptions = {
  enabled?: boolean;
  target?: KeyboardShortcutTarget;
};

function normalizeShortcutKey(key: string): string {
  return key.length === 1 ? key.toLowerCase() : key;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  const role = target.getAttribute("role");
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || role === "textbox";
}

export function matchesKeyboardShortcut(event: Pick<KeyboardEvent, "altKey" | "ctrlKey" | "key" | "metaKey" | "shiftKey">, binding: Omit<KeyboardShortcutBinding, "handler">): boolean {
  return normalizeShortcutKey(event.key) === normalizeShortcutKey(binding.key)
    && event.altKey === Boolean(binding.altKey)
    && event.ctrlKey === Boolean(binding.ctrlKey)
    && event.metaKey === Boolean(binding.metaKey)
    && event.shiftKey === Boolean(binding.shiftKey);
}

export function useKeyboardShortcuts(bindings: KeyboardShortcutBinding[], options?: UseKeyboardShortcutsOptions) {
  const bindingsRef = useRef(bindings);

  useEffect(() => {
    bindingsRef.current = bindings;
  }, [bindings]);

  useEffect(() => {
    if (options?.enabled === false) {
      return;
    }

    const target = options?.target ?? document;
    if (!target) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      for (const binding of bindingsRef.current) {
        if (binding.enabled === false) {
          continue;
        }
        if (!binding.allowInEditable && isEditableTarget(event.target)) {
          continue;
        }
        if (!matchesKeyboardShortcut(event, binding)) {
          continue;
        }
        if (binding.preventDefault) {
          event.preventDefault();
        }
        binding.handler(event);
        return;
      }
    }

    const eventListener: EventListener = (event) => {
      if (!(event instanceof KeyboardEvent)) {
        return;
      }
      handleKeyDown(event);
    };

    target.addEventListener("keydown", eventListener);
    return () => {
      target.removeEventListener("keydown", eventListener);
    };
  }, [options?.enabled, options?.target]);
}