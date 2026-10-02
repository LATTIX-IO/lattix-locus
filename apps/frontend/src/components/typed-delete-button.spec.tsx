import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TypedDeleteButton } from "@/components/typed-delete-button";

const {
  refreshMock,
  deleteWorkflowDefinitionMock,
  deleteAgentDefinitionMock,
  deleteGuardrailRulesetMock,
  deleteNodeDefinitionMock,
} = vi.hoisted(() => ({
  refreshMock: vi.fn(),
  deleteWorkflowDefinitionMock: vi.fn(),
  deleteAgentDefinitionMock: vi.fn(),
  deleteGuardrailRulesetMock: vi.fn(),
  deleteNodeDefinitionMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    refresh: refreshMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  deleteWorkflowDefinition: deleteWorkflowDefinitionMock,
  deleteAgentDefinition: deleteAgentDefinitionMock,
  deleteGuardrailRuleset: deleteGuardrailRulesetMock,
  deleteNodeDefinition: deleteNodeDefinitionMock,
}));

describe("TypedDeleteButton", () => {
  beforeEach(() => {
    refreshMock.mockReset();
    deleteWorkflowDefinitionMock.mockReset();
    deleteAgentDefinitionMock.mockReset();
    deleteGuardrailRulesetMock.mockReset();
    deleteNodeDefinitionMock.mockReset();

    deleteWorkflowDefinitionMock.mockResolvedValue({ ok: true });
    deleteAgentDefinitionMock.mockResolvedValue({ ok: true });
    deleteGuardrailRulesetMock.mockResolvedValue({ ok: true });
    deleteNodeDefinitionMock.mockResolvedValue({ ok: true });
  });

  it("requires the exact name and keeps the modal open on backend failure", async () => {
    deleteNodeDefinitionMock.mockRejectedValueOnce(new Error("501 node definitions are read-only"));

    render(<TypedDeleteButton itemType="node" itemId="frontier/router" itemName="Router Node" />);

    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const confirmButton = screen.getAllByRole("button", { name: /^delete$/i })[1];
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/type exact name to confirm/i), { target: { value: "Router Node" } });
    expect(confirmButton).not.toBeDisabled();
    fireEvent.click(confirmButton);

    expect(await screen.findByText("501 node definitions are read-only")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /confirm deletion/i })).toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("clears errors on cancel and invokes onDeleted after a successful retry", async () => {
    const onDeletedMock = vi.fn();
    deleteGuardrailRulesetMock
      .mockRejectedValueOnce(new Error("409 guardrail still referenced"))
      .mockResolvedValueOnce({ ok: true });

    render(
      <TypedDeleteButton
        itemType="guardrail"
        itemId="guardrail-1"
        itemName="Core Guardrails"
        onDeleted={onDeletedMock}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    fireEvent.change(screen.getByPlaceholderText(/type exact name to confirm/i), { target: { value: "Core Guardrails" } });
    fireEvent.click(screen.getAllByRole("button", { name: /^delete$/i })[1]);

    expect(await screen.findByText("409 guardrail still referenced")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    expect(screen.queryByText("409 guardrail still referenced")).not.toBeInTheDocument();
    expect((screen.getByPlaceholderText(/type exact name to confirm/i) as HTMLInputElement).value).toBe("");

    fireEvent.change(screen.getByPlaceholderText(/type exact name to confirm/i), { target: { value: "Core Guardrails" } });
    fireEvent.click(screen.getAllByRole("button", { name: /^delete$/i })[1]);

    await waitFor(() => expect(onDeletedMock).toHaveBeenCalledWith("guardrail-1"));
    expect(refreshMock).not.toHaveBeenCalled();
  });
});
