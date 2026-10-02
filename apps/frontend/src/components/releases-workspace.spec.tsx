import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReleasesWorkspace } from "@/components/releases-workspace";

const {
  refreshMock,
  addToastMock,
  getWorkflowDefinitionVersionsMock,
  getAgentDefinitionVersionsMock,
  getGuardrailRulesetVersionsMock,
  activateWorkflowDefinitionMock,
  activateAgentDefinitionMock,
  activateGuardrailRulesetMock,
  rollbackWorkflowDefinitionMock,
  rollbackAgentDefinitionMock,
  rollbackGuardrailRulesetMock,
} = vi.hoisted(() => ({
  refreshMock: vi.fn(),
  addToastMock: vi.fn(),
  getWorkflowDefinitionVersionsMock: vi.fn(),
  getAgentDefinitionVersionsMock: vi.fn(),
  getGuardrailRulesetVersionsMock: vi.fn(),
  activateWorkflowDefinitionMock: vi.fn(),
  activateAgentDefinitionMock: vi.fn(),
  activateGuardrailRulesetMock: vi.fn(),
  rollbackWorkflowDefinitionMock: vi.fn(),
  rollbackAgentDefinitionMock: vi.fn(),
  rollbackGuardrailRulesetMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    refresh: refreshMock,
  }),
}));

vi.mock("@/components/toast", () => ({
  useToast: () => ({
    addToast: addToastMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  getWorkflowDefinitionVersions: getWorkflowDefinitionVersionsMock,
  getAgentDefinitionVersions: getAgentDefinitionVersionsMock,
  getGuardrailRulesetVersions: getGuardrailRulesetVersionsMock,
  activateWorkflowDefinition: activateWorkflowDefinitionMock,
  activateAgentDefinition: activateAgentDefinitionMock,
  activateGuardrailRuleset: activateGuardrailRulesetMock,
  rollbackWorkflowDefinition: rollbackWorkflowDefinitionMock,
  rollbackAgentDefinition: rollbackAgentDefinitionMock,
  rollbackGuardrailRuleset: rollbackGuardrailRulesetMock,
}));

describe("ReleasesWorkspace", () => {
  beforeEach(() => {
    refreshMock.mockReset();
    addToastMock.mockReset();
    getWorkflowDefinitionVersionsMock.mockReset();
    getAgentDefinitionVersionsMock.mockReset();
    getGuardrailRulesetVersionsMock.mockReset();
    activateWorkflowDefinitionMock.mockReset();
    activateAgentDefinitionMock.mockReset();
    activateGuardrailRulesetMock.mockReset();
    rollbackWorkflowDefinitionMock.mockReset();
    rollbackAgentDefinitionMock.mockReset();
    rollbackGuardrailRulesetMock.mockReset();

    getWorkflowDefinitionVersionsMock.mockResolvedValue({
      count: 2,
      versions: [
        {
          id: "wf-rev-2",
          entity_type: "workflow_definition",
          entity_id: "wf-1",
          revision: 2,
          action: "publish",
          version: 3,
          status: "published",
          created_at: "2026-04-07T00:00:00Z",
          actor: "builder",
          metadata: {},
        },
        {
          id: "wf-rev-1",
          entity_type: "workflow_definition",
          entity_id: "wf-1",
          revision: 1,
          action: "save",
          version: 2,
          status: "draft",
          created_at: "2026-04-06T00:00:00Z",
          actor: "builder",
          metadata: {},
        },
      ],
    });
    activateWorkflowDefinitionMock.mockResolvedValue({
      ok: true,
      id: "wf-1",
      active_revision: {
        id: "wf-rev-2",
        entity_type: "workflow_definition",
        entity_id: "wf-1",
        revision: 2,
        action: "publish",
        version: 3,
        status: "published",
        created_at: "2026-04-07T00:00:00Z",
        actor: "builder",
        metadata: {},
      },
      activation_revision: {
        id: "wf-rev-3",
        entity_type: "workflow_definition",
        entity_id: "wf-1",
        revision: 3,
        action: "activate",
        version: 3,
        status: "published",
        created_at: "2026-04-07T00:01:00Z",
        actor: "builder",
        metadata: {},
      },
    });
    getAgentDefinitionVersionsMock.mockResolvedValue({
      count: 1,
      versions: [
        {
          id: "agent-rev-2",
          entity_type: "agent_definition",
          entity_id: "agent-1",
          revision: 2,
          action: "publish",
          version: 5,
          status: "published",
          created_at: "2026-04-07T00:00:00Z",
          actor: "builder",
          metadata: {},
        },
      ],
    });
    activateAgentDefinitionMock.mockResolvedValue({
      ok: true,
      id: "agent-1",
      active_revision: {
        id: "agent-rev-2",
        entity_type: "agent_definition",
        entity_id: "agent-1",
        revision: 2,
        action: "publish",
        version: 5,
        status: "published",
        created_at: "2026-04-07T00:00:00Z",
        actor: "builder",
        metadata: {},
      },
      activation_revision: {
        id: "agent-rev-3",
        entity_type: "agent_definition",
        entity_id: "agent-1",
        revision: 3,
        action: "activate",
        version: 5,
        status: "published",
        created_at: "2026-04-07T00:01:00Z",
        actor: "builder",
        metadata: {},
      },
    });
    getGuardrailRulesetVersionsMock.mockResolvedValue({
      count: 1,
      versions: [
        {
          id: "guardrail-rev-1",
          entity_type: "guardrail_ruleset",
          entity_id: "guardrail-1",
          revision: 1,
          action: "publish",
          version: 4,
          status: "published",
          created_at: "2026-04-05T00:00:00Z",
          actor: "builder",
          metadata: {},
        },
      ],
    });
    rollbackGuardrailRulesetMock.mockResolvedValue({
      ok: true,
      id: "guardrail-1",
      version: 4,
      status: "published",
      restored_from: {
        id: "guardrail-rev-1",
        entity_type: "guardrail_ruleset",
        entity_id: "guardrail-1",
        revision: 1,
        action: "publish",
        version: 4,
        status: "published",
        created_at: "2026-04-05T00:00:00Z",
        actor: "builder",
        metadata: {},
      },
      revision: {
        id: "guardrail-rev-2",
        entity_type: "guardrail_ruleset",
        entity_id: "guardrail-1",
        revision: 2,
        action: "rollback",
        version: 4,
        status: "published",
        created_at: "2026-04-07T00:00:00Z",
        actor: "builder",
        metadata: {},
      },
    });
    rollbackWorkflowDefinitionMock.mockResolvedValue({
      ok: true,
      id: "wf-1",
      version: 4,
      status: "draft",
      restored_from: {
        id: "wf-rev-1",
        entity_type: "workflow_definition",
        entity_id: "wf-1",
        revision: 1,
        action: "save",
        version: 2,
        status: "draft",
        created_at: "2026-04-06T00:00:00Z",
        actor: "builder",
        metadata: {},
      },
      revision: {
        id: "wf-rev-4",
        entity_type: "workflow_definition",
        entity_id: "wf-1",
        revision: 4,
        action: "rollback",
        version: 4,
        status: "draft",
        created_at: "2026-04-07T00:02:00Z",
        actor: "builder",
        metadata: {},
      },
    });
  });

  it("loads revisions and promotes a published workflow revision", async () => {
    render(
      <ReleasesWorkspace
        workflows={[
          {
            id: "wf-1",
            name: "Incident Workflow",
            description: "",
            version: 3,
            status: "published",
            published_revision_id: "wf-rev-2",
            active_revision_id: "wf-rev-1",
          },
        ]}
        agents={[]}
        guardrails={[]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /show revisions/i }));

    await waitFor(() => {
      expect(getWorkflowDefinitionVersionsMock).toHaveBeenCalledWith("wf-1");
    });
    expect(await screen.findByText("r2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^promote$/i }));

    await waitFor(() => {
      expect(activateWorkflowDefinitionMock).toHaveBeenCalledWith("wf-1", {});
    });
    expect(screen.getByText("active: wf-rev-2")).toBeInTheDocument();
    expect(refreshMock).toHaveBeenCalled();
    expect(addToastMock).toHaveBeenCalledWith("success", "Workflow runtime revision activated.");
  });

  it("promotes a published agent revision", async () => {
    render(
      <ReleasesWorkspace
        workflows={[]}
        agents={[
          {
            id: "agent-1",
            name: "Security Agent",
            version: 5,
            status: "published",
            type: "graph",
            published_revision_id: "agent-rev-2",
            active_revision_id: null,
          },
        ]}
        guardrails={[]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^promote$/i }));

    await waitFor(() => {
      expect(activateAgentDefinitionMock).toHaveBeenCalledWith("agent-1", {});
    });
    expect(addToastMock).toHaveBeenCalledWith("success", "Agent runtime revision activated.");
  });

  it("restores a selected guardrail revision", async () => {
    render(
      <ReleasesWorkspace
        workflows={[]}
        agents={[]}
        guardrails={[
          {
            id: "guardrail-1",
            name: "Core Guardrails",
            version: 4,
            status: "published",
            published_revision_id: "guardrail-rev-1",
            active_revision_id: "guardrail-rev-1",
            config_json: {},
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /show revisions/i }));

    await waitFor(() => {
      expect(getGuardrailRulesetVersionsMock).toHaveBeenCalledWith("guardrail-1");
    });
    fireEvent.click(screen.getByRole("button", { name: /^restore$/i }));

    await waitFor(() => {
      expect(rollbackGuardrailRulesetMock).toHaveBeenCalledWith("guardrail-1", { revision_id: "guardrail-rev-1" });
    });
    expect(refreshMock).toHaveBeenCalled();
    expect(addToastMock).toHaveBeenCalledWith("success", "Guardrail restored from the selected revision.");
  });

  it("updates workflow release badges and actions immediately after rollback", async () => {
    render(
      <ReleasesWorkspace
        workflows={[
          {
            id: "wf-1",
            name: "Incident Workflow",
            description: "",
            version: 3,
            status: "published",
            published_revision_id: "wf-rev-2",
            active_revision_id: "wf-rev-2",
          },
        ]}
        agents={[]}
        guardrails={[]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /show revisions/i }));

    await waitFor(() => {
      expect(getWorkflowDefinitionVersionsMock).toHaveBeenCalledWith("wf-1");
    });

    const draftRow = screen.getByText("r1").closest("tr");
    expect(draftRow).not.toBeNull();
    fireEvent.click(within(draftRow as HTMLTableRowElement).getByRole("button", { name: /^restore$/i }));

    await waitFor(() => {
      expect(rollbackWorkflowDefinitionMock).toHaveBeenCalledWith("wf-1", { revision_id: "wf-rev-1" });
    });

    expect(screen.getByText("published: none")).toBeInTheDocument();
    expect(screen.getByText("v4")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^promote$/i })).toBeDisabled();
  });
});
