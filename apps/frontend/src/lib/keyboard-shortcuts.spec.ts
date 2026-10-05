import { createElement } from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { matchesKeyboardShortcut, useKeyboardShortcuts } from "@/lib/keyboard-shortcuts";

function ShortcutHarness({ onTrigger }: { onTrigger: () => void }) {
  useKeyboardShortcuts([
    {
      key: "k",
      metaKey: true,
      preventDefault: true,
      handler: onTrigger,
    },
  ]);

  return createElement("textarea", { "aria-label": "shortcut-target" });
}

describe("keyboard-shortcuts", () => {
  it("matches keyboard shortcuts case-insensitively for single-character keys", () => {
    expect(matchesKeyboardShortcut(
      {
        key: "K",
        metaKey: true,
        ctrlKey: false,
        altKey: false,
        shiftKey: false,
      } as KeyboardEvent,
      {
        key: "k",
        metaKey: true,
      },
    )).toBe(true);
  });

  it("ignores shortcuts fired from editable fields unless explicitly allowed", () => {
    const onTrigger = vi.fn();
    render(createElement(ShortcutHarness, { onTrigger }));

    const target = document.querySelector("textarea");
    expect(target).not.toBeNull();
    const event = new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    });
    target?.dispatchEvent(event);

    expect(onTrigger).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });
});