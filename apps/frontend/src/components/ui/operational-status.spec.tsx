import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { OperationalStatus } from "@/components/ui/operational-status";

describe("OperationalStatus", () => {
  it.each([
    ["normal", "●"],
    ["warning", "◆"],
    ["critical", "▲"],
    ["unknown", "◌"],
    ["simulation", "□"],
  ] as const)("renders %s with explicit label and shape", (tone, shape) => {
    const { container } = render(<OperationalStatus tone={tone} label={tone + " state"} />);
    expect(screen.getByText(tone + " state")).toBeTruthy();
    expect(container.querySelector('[data-slot="operational-status"]')?.getAttribute("data-tone")).toBe(tone);
    expect(container.querySelector('[aria-hidden="true"]')?.textContent).toBe(shape);
  });
});
