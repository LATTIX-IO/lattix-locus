import { describe, expect, it } from "vitest";
import nextConfig from "../../../next.config";
import { PRIMARY_NAV, activeNavItem, isNavLinkActive } from "@/components/navigation/nav-config";

describe("primary navigation", () => {
  it("is one list of five areas with no modes", () => {
    expect(PRIMARY_NAV.map((item) => item.label)).toEqual(["Home", "Activity", "Memory", "Library", "Settings"]);
    expect(PRIMARY_NAV.map((item) => item.href)).toEqual(["/home", "/activity", "/memory", "/library", "/settings"]);
  });

  it("assigns each page to its area", () => {
    expect(activeNavItem("/home")?.label).toBe("Home");
    expect(activeNavItem("/activity")?.label).toBe("Activity");
    expect(activeNavItem("/activity/traces")?.label).toBe("Activity");
    expect(activeNavItem("/artifacts/a-1")?.label).toBe("Activity");
    expect(activeNavItem("/library/skills/s-1")?.label).toBe("Library");
    expect(activeNavItem("/workflows/start")?.label).toBe("Library");
    expect(activeNavItem("/settings")?.label).toBe("Settings");
    expect(activeNavItem("/auth")).toBeNull();
  });

  it("marks Runs active only on /activity itself", () => {
    const runs = { href: "/activity", label: "Runs" };
    expect(isNavLinkActive("/activity", runs, { exact: true })).toBe(true);
    expect(isNavLinkActive("/activity/traces", runs, { exact: true })).toBe(false);
  });
});

describe("legacy route redirects", () => {
  async function redirectsBySource() {
    const rules = (await nextConfig.redirects?.()) ?? [];
    return rules;
  }

  function resolve(rules: Awaited<ReturnType<typeof redirectsBySource>>, source: string, query?: Record<string, string>) {
    const rule = rules.find(
      (candidate) =>
        candidate.source === source &&
        (candidate.has ?? []).every((condition) => condition.type === "query" && query?.[condition.key] === condition.value),
    );
    return rule?.destination;
  }

  it("sends the old operator routes to their new homes", async () => {
    const rules = await redirectsBySource();
    expect(resolve(rules, "/inbox")).toBe("/activity");
    expect(resolve(rules, "/runs/:id")).toBe("/activity?session=:id&details=1");
    expect(resolve(rules, "/tasks/:id")).toBe("/activity?session=:id");
    expect(resolve(rules, "/playbooks")).toBe("/library/playbooks");
    expect(resolve(rules, "/guardrails")).toBe("/library/guardrails");
  });

  it("sends every /builder route to Library, Activity or Settings", async () => {
    const rules = await redirectsBySource();
    expect(resolve(rules, "/builder")).toBe("/library");
    expect(resolve(rules, "/builder/workflows/:path*")).toBe("/library/workflows/:path*");
    expect(resolve(rules, "/builder/agents")).toBe("/library/agents");
    expect(resolve(rules, "/builder/integrations")).toBe("/library/connections");
    expect(resolve(rules, "/builder/observability")).toBe("/activity/traces");
    expect(resolve(rules, "/builder/models")).toBe("/settings?section=engines");
    expect(resolve(rules, "/builder/settings/runtime")).toBe("/settings?section=policies");
    expect(resolve(rules, "/builder/settings/governance")).toBe("/settings?section=policies");
  });

  it("maps the old provider tab to Engines and everything else to Settings", async () => {
    const rules = await redirectsBySource();
    const settingsRules = rules.filter((rule) => rule.source === "/builder/settings");
    // The query-matched rule must come first so Next picks it.
    expect(settingsRules[0]).toMatchObject({ has: [{ type: "query", key: "tab", value: "providers" }], destination: "/settings?section=engines" });
    expect(settingsRules.at(-1)?.destination).toBe("/settings");
    expect(rules.every((rule) => rule.permanent === false)).toBe(true);
  });
});
