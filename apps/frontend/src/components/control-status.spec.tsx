import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ControlStatusBadge, ControlStatusList, normalizeControlState } from "@/components/control-status";
import type { ControlStatusReport } from "@/types/locus";

const report: ControlStatusReport = {
  controls: [
    { id: "execution_sandbox", label: "Agent execution sandbox", state: "enforced", evidence: "tier kernel-bwrap" },
    { id: "egress_allowlist", label: "Egress allowlist", state: "degraded", evidence: "node checks only" },
    { id: "policy_engine_rego", label: "Policy engine (Rego)", state: "off", evidence: "OPA server never called" },
    { id: "secret_broker_vault", label: "Secret broker (Vault)", state: "unverified", evidence: "VAULT_ADDR set" },
  ],
  summary: { enforced: 1, degraded: 1, off: 1, unverified: 1 },
};

describe("ControlStatusBadge", () => {
  it.each([
    ["enforced", "Enforced"],
    ["degraded", "Degraded"],
    ["off", "Off"],
    ["unverified", "Unverified"],
  ])("renders %s as %s", (state, label) => {
    render(<ControlStatusBadge state={state} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("never upgrades an unknown state to enforced", () => {
    render(<ControlStatusBadge state="active" />);
    expect(screen.getByText("Unverified")).toBeInTheDocument();
    expect(screen.queryByText("Enforced")).not.toBeInTheDocument();
    expect(normalizeControlState(undefined)).toBe("unverified");
  });
});

describe("ControlStatusList", () => {
  it("renders each control with the state and evidence reported by the API", () => {
    render(<ControlStatusList report={report} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(4);
    const byId = Object.fromEntries(items.map((item) => [item.getAttribute("data-control-id"), item]));

    expect(within(byId.execution_sandbox).getByText("Enforced")).toBeInTheDocument();
    expect(within(byId.egress_allowlist).getByText("Degraded")).toBeInTheDocument();
    expect(within(byId.policy_engine_rego).getByText("Off")).toBeInTheDocument();
    expect(within(byId.secret_broker_vault).getByText("Unverified")).toBeInTheDocument();
    expect(screen.getByText("OPA server never called")).toBeInTheDocument();
    expect(screen.getAllByText("Enforced")).toHaveLength(1);
  });

  it("can hide evidence and shows an empty message without a report", () => {
    const { rerender } = render(<ControlStatusList report={report} showEvidence={false} />);
    expect(screen.queryByText("OPA server never called")).not.toBeInTheDocument();
    rerender(<ControlStatusList report={null} emptyMessage="No status" />);
    expect(screen.getByText("No status")).toBeInTheDocument();
  });
});
