import { describe, expect, it } from "vitest";
import { resolveFloatingMenuPosition } from "@/lib/floating-menu";

describe("resolveFloatingMenuPosition", () => {
  it("clamps menus back inside the viewport when opened near the lower-right edge", () => {
    expect(resolveFloatingMenuPosition({
      anchorX: 980,
      anchorY: 760,
      menuWidth: 144,
      menuHeight: 84,
      viewportWidth: 1000,
      viewportHeight: 800,
    })).toEqual({
      left: 844,
      top: 704,
    });
  });

  it("keeps menus anchored when enough viewport space is available", () => {
    expect(resolveFloatingMenuPosition({
      anchorX: 320,
      anchorY: 240,
      menuWidth: 144,
      menuHeight: 84,
      viewportWidth: 1280,
      viewportHeight: 900,
    })).toEqual({
      left: 320,
      top: 240,
    });
  });
});