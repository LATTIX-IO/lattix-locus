import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("blended Lattix design token contract", () => {
  const css = readFileSync(join(process.cwd(), "src/app/globals.css"), "utf8");
  it("keeps semantic interaction blue and status separate", () => {
    expect(css).toContain("--primary: 219 75% 48%");
    expect(css).toContain("--state-critical: 358 75% 59%");
    expect(css).toContain("--lattix-simulation: #7c5ce0");
  });
  it("uses locally hosted IBM Plex and no externally fetched runtime fonts", () => {
    expect(css).toContain('/fonts/ibm-plex/ibm-plex-sans-latin-400.woff2');
    expect(css).toContain('/fonts/ibm-plex/ibm-plex-mono-latin-400.woff2');
    expect(css).not.toMatch(/fonts\.googleapis\.com/);
  });
  it("preserves Radix/shadcn HSL aliases", () => {
    expect(css).toContain("--color-primary: hsl(var(--primary));");
    expect(css).toContain("--color-card: hsl(var(--card));");
  });
});
