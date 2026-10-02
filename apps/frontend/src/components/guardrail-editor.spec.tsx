import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GuardrailEditor } from "@/components/guardrail-editor";

const { replaceMock, refreshMock, saveGuardrailRulesetMock, publishGuardrailRulesetMock } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  refreshMock: vi.fn(),
  saveGuardrailRulesetMock: vi.fn(),
  publishGuardrailRulesetMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: replaceMock,
    refresh: refreshMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  saveGuardrailRuleset: saveGuardrailRulesetMock,
  publishGuardrailRuleset: publishGuardrailRulesetMock,
}));

describe("GuardrailEditor", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    refreshMock.mockReset();
    saveGuardrailRulesetMock.mockReset();
    publishGuardrailRulesetMock.mockReset();

    saveGuardrailRulesetMock.mockResolvedValue({ ok: true, id: "guardrail-123" });
    publishGuardrailRulesetMock.mockResolvedValue({ ok: true });
  });

  it("filters controls and persists the configured guardrail payload", async () => {
    render(<GuardrailEditor mode="new" />);

    fireEvent.change(screen.getByLabelText(/risk group filter/i), { target: { value: "Protected materials" } });

    expect(screen.getByText("Protected material for code")).toBeInTheDocument();
    expect(screen.queryByText("Hate: Medium blocking")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/guardrail set name/i), { target: { value: "Secure Messaging" } });
    fireEvent.change(screen.getByLabelText(/blocked keywords/i), { target: { value: "secret, token" } });
    fireEvent.change(screen.getByLabelText(/required keywords/i), { target: { value: "approved" } });
    fireEvent.change(screen.getByLabelText(/min length/i), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText(/max length/i), { target: { value: "200" } });
    fireEvent.change(screen.getByLabelText(/reject message/i), { target: { value: "Rejected by policy" } });
    fireEvent.change(screen.getByLabelText(/^models$/i), { target: { value: "gpt-5.2-mini" } });
    fireEvent.change(screen.getByLabelText(/^workflows$/i), { target: { value: "investor-outreach-pack" } });
    fireEvent.click(screen.getByLabelText(/detect secrets in payload/i));
    fireEvent.click(screen.getByRole("button", { name: /save draft/i }));

    await waitFor(() => expect(saveGuardrailRulesetMock).toHaveBeenCalledTimes(1));
    expect(saveGuardrailRulesetMock).toHaveBeenCalledWith(expect.objectContaining({
      name: "Secure Messaging",
      config_json: expect.objectContaining({
        blocked_keywords: ["secret", "token"],
        required_keywords: ["approved"],
        min_length: 10,
        max_length: 200,
        reject_message: "Rejected by policy",
        apply_model: "gpt-5.2-mini",
        apply_workflow: "investor-outreach-pack",
        detect_secrets: false,
      }),
    }));
    expect(replaceMock).toHaveBeenCalledWith("/builder/guardrails/guardrail-123");
  });

  it("surfaces publish failures for existing guardrail rulesets", async () => {
    publishGuardrailRulesetMock.mockRejectedValueOnce(new Error("403 publish denied"));

    render(
      <GuardrailEditor
        mode="edit"
        ruleset={{
          id: "guardrail-42",
          name: "Policy Rules",
          version: 2,
          status: "draft",
          config_json: {},
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^publish$/i }));

    expect(await screen.findByText("403 publish denied")).toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });
});
