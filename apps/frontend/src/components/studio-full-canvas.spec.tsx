import type { ComponentProps, ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StudioFullCanvas } from "@/components/studio-full-canvas";

const reactFlowCanvasSpy = vi.fn();
const autoLayoutSpy = vi.fn();
const routerPushSpy = vi.fn();
const { getIntegrationsMock, getMcpConnectionsMock, getNodeDefinitionsMock, getUserSkillsMock, runGraphMock } = vi.hoisted(() => ({
  getIntegrationsMock: vi.fn(async () => ([
    {
      id: "int-incident",
      name: "Incident Connector",
      type: "http",
      status: "configured",
      base_url: "https://incident.example.com",
      auth_type: "none",
      secret_ref: "",
      capabilities: ["/incident-triage", "ops"],
    },
    {
      id: "int-oncall",
      name: "Oncall Connector",
      type: "http",
      status: "draft",
      base_url: "https://oncall.example.com",
      auth_type: "none",
      secret_ref: "",
      capabilities: ["tenant-oncall"],
    },
    {
      id: "int-billing",
      name: "Billing Connector",
      type: "http",
      status: "configured",
      base_url: "https://billing.example.com",
      auth_type: "none",
      secret_ref: "",
      capabilities: ["finance"],
    },
  ])),
  getMcpConnectionsMock: vi.fn(async () => ([
    {
      id: "mcp-github-approved",
      starter_id: "github",
      wave: 1,
      name: "GitHub MCP",
      status: "approved",
      server_url: "http://localhost:7071/mcp/github",
      transport: "streamable_http",
      auth_type: "bearer",
      secret_ref: "secret/integrations/mcp/github/token",
    },
    {
      id: "mcp-github-draft",
      starter_id: "github",
      wave: 1,
      name: "Draft GitHub MCP",
      status: "draft",
      server_url: "http://localhost:7071/mcp/github-draft",
      transport: "streamable_http",
      auth_type: "bearer",
      secret_ref: "secret/integrations/mcp/github/token",
    },
  ])),
  getNodeDefinitionsMock: vi.fn(async () => []),
  getUserSkillsMock: vi.fn(async () => ({
    principal_id: "user-1",
    skills: ["/personal-research", "/incident-triage"],
  })),
  runGraphMock: vi.fn(async () => ({
    run_id: "r1",
    status: "completed",
    execution_order: [],
    node_results: {},
    events: [],
    validation: { valid: true, issues: [] },
    runtime: {
      requested_engine: "native",
      selected_engine: "native",
      executed_engine: "native",
      mode: "native",
    },
  })),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: routerPushSpy,
  }),
}));

vi.mock("@/lib/api", () => ({
  getIntegrations: getIntegrationsMock,
  getMcpConnections: getMcpConnectionsMock,
  getNodeDefinitions: getNodeDefinitionsMock,
  getCollaborationSession: vi.fn(async () => ({
    id: "collab-1",
    version: 1,
    participants: [],
    graph_json: { nodes: [], links: [] },
  })),
  getGuardrailRulesets: vi.fn(async () => []),
  getObservabilityDashboard: vi.fn(async () => ({
    summary: {
      total_runs: 0,
      token_estimate: 0,
      cost_estimate_usd: 0,
      average_latency_ms: 0,
    },
    runs: [],
  })),
  getObservabilityRunTrace: vi.fn(async () => ({
    run_id: "r1",
    status: "completed",
    event_count: 0,
    node_count: 0,
    edge_count: 0,
  })),
  getRuntimeProviders: vi.fn(async () => ({
    providers: [],
  })),
  getPlatformSettings: vi.fn(async () => ({
    local_only_mode: true,
    mask_secrets_in_events: true,
    require_human_approval: false,
    default_guardrail_ruleset_id: null,
    global_blocked_keywords: [],
    tenant_scoped_skills: ["/tenant-oncall", "/incident-triage"],
    collaboration_max_agents: 8,
  })),
  getUserSkills: getUserSkillsMock,
  getMemorySession: vi.fn(async () => ({ session_id: "s", count: 0, entries: [] })),
  clearMemorySession: vi.fn(async () => ({ ok: true, session_id: "s" })),
  joinCollaborationSession: vi.fn(async () => ({
    session: { id: "collab-1", version: 1, participants: [], graph_json: { nodes: [], links: [] } },
    participant: { role: "editor" },
  })),
  syncCollaborationSession: vi.fn(async () => ({ version: 2 })),
  validateGraph: vi.fn(async () => ({ valid: true, issues: [] })),
  runGraph: runGraphMock,
}));

vi.mock("@/components/reactflow-canvas", () => ({
  ReactFlowCanvas: (props: {
    onReady?: (api: {
      addNode: (node: { type: string; title?: string; x?: number; y?: number; config?: Record<string, unknown> }) => void;
      autoLayout: (options?: { fitView?: boolean }) => void;
      clear: () => void;
      serialize: () => { nodes: Array<unknown>; links: Array<unknown> };
    }) => void;
  }) => {
    reactFlowCanvasSpy(props);
    props.onReady?.({
      addNode: () => {},
      autoLayout: autoLayoutSpy,
      clear: () => {},
      serialize: () => ({ nodes: [], links: [] }),
    });
    return <div data-testid="rf-canvas" />;
  },
}));

async function renderStudioFullCanvas(props: ComponentProps<typeof StudioFullCanvas>) {
  let rendered: ReturnType<typeof render> | undefined;

  await act(async () => {
    rendered = render(<StudioFullCanvas {...props} />);
    await Promise.resolve();
  });

  await waitFor(() => expect(getNodeDefinitionsMock).toHaveBeenCalled());
  return rendered as ReturnType<typeof render>;
}

describe("StudioFullCanvas", () => {
  beforeEach(() => {
    reactFlowCanvasSpy.mockClear();
    autoLayoutSpy.mockClear();
    getIntegrationsMock.mockClear();
    getMcpConnectionsMock.mockClear();
    getNodeDefinitionsMock.mockClear();
    getUserSkillsMock.mockClear();
    runGraphMock.mockClear();
    routerPushSpy.mockClear();
  });

  it("merges user and tenant skill suggestions into agent widget overrides", async () => {
    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      externalWidgetOptionOverrides: { agent: { agent_id: ["agent-1"] } },
      onSave: async () => {},
      onPublish: async () => {},
    });

    await waitFor(() => expect(getUserSkillsMock).toHaveBeenCalledTimes(1));

    const lastCall = reactFlowCanvasSpy.mock.calls[reactFlowCanvasSpy.mock.calls.length - 1]?.[0] as {
      widgetOptionOverrides?: Record<string, Record<string, string[]>>;
    };

    expect(lastCall.widgetOptionOverrides?.agent?.agent_id).toEqual(["agent-1"]);
    expect(lastCall.widgetOptionOverrides?.agent?.skills).toEqual([
      "/personal-research",
      "/incident-triage",
      "/tenant-oncall",
    ]);
    expect(lastCall.widgetOptionOverrides?.["tool-call"]?.tool_id).toEqual([
      "int-incident",
      "int-oncall",
      "tool/unspecified",
      "tool/search",
      "tool/http",
      "tool/retrieval",
      "tool/code",
      "tool/sql",
      "tool/file",
      "tool/email",
      "tool/slack",
      "tool/mcp",
      "int-billing",
    ]);
    expect(lastCall.widgetOptionOverrides?.["tool-call"]?.mcp_connection_id).toEqual([
      "mcp-github-approved",
    ]);
  });

  it("hides internal runtime controls in standard mode", async () => {
    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave: async () => {},
      onPublish: async () => {},
    });

    expect(await screen.findByText(/Execution Profile/i)).toBeInTheDocument();
    expect(screen.queryByText(/Framework adapters/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/runtime engine/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/runtime session id/i)).not.toBeInTheDocument();
  });

  it("applies overflow-safe viewport classes", async () => {
    const { container } = await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave: async () => {},
      onPublish: async () => {},
    });

    const section = container.querySelector("section");
    expect(section).not.toBeNull();
    expect(section?.className).toContain("overflow-hidden");
    expect(section?.className).toContain("h-[calc(100vh-var(--fx-content-top,57px))]");
  });

  it("passes selected edge style controls to canvas", async () => {
    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave: async () => {},
      onPublish: async () => {},
    });

    const edgeSelect = screen.getByRole("combobox", { name: /edge style/i });
    expect(edgeSelect).toHaveValue("default");

    fireEvent.change(edgeSelect, { target: { value: "step" } });

    const animateToggle = screen.getByRole("checkbox", { name: /animate/i });
    fireEvent.click(animateToggle);

    const lastCall = reactFlowCanvasSpy.mock.calls[reactFlowCanvasSpy.mock.calls.length - 1]?.[0] as {
      edgeType?: string;
      edgeAnimated?: boolean;
    };

    expect(lastCall.edgeType).toBe("step");
    expect(lastCall.edgeAnimated).toBe(false);
  });

  it("triggers auto layout from toolbar", async () => {
    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [{ id: "n1", title: "Trigger", type: "locus/trigger", x: 0, y: 0, config: {} }],
      initialLinks: [],
      onSave: async () => {},
      onPublish: async () => {},
    });

    fireEvent.click(screen.getByRole("button", { name: /auto layout/i }));

    expect(autoLayoutSpy).toHaveBeenCalledWith({ fitView: true });
  });

  it("shows the model runtime panel without engine or strategy pickers", async () => {
    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      builderMode: "internal",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave: async () => {},
      onPublish: async () => {},
    });

    expect(await screen.findByText("Model Runtime")).toBeInTheDocument();
    expect(await screen.findByLabelText(/runtime model/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/runtime engine/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/runtime strategy/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Framework adapters")).not.toBeInTheDocument();
    expect(getNodeDefinitionsMock).toHaveBeenCalledWith({ includeInternal: true });
  });

  it("sends no engine selection in the graph run payload", async () => {
    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      builderMode: "internal",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave: async () => {},
      onPublish: async () => {},
    });

    fireEvent.click(await screen.findByRole("button", { name: /run test/i }));

    await waitFor(() => expect(runGraphMock).toHaveBeenCalledTimes(1));
    const calls = runGraphMock.mock.calls as unknown as Array<[unknown]>;
    const call = calls[0]?.[0] as { input?: { runtime?: Record<string, unknown> } } | undefined;
    expect(call?.input?.runtime).toBeDefined();
    expect(call?.input?.runtime).not.toHaveProperty("engine");
    expect(call?.input?.runtime).not.toHaveProperty("strategy");
    expect(call?.input?.runtime).not.toHaveProperty("hybrid_routing");
  });

  it("supports playbook mode without a publish action", async () => {
    await renderStudioFullCanvas({
      entityType: "playbook",
      entityId: "playbook-1",
      entityName: "Ops Playbook",
      description: "playbook summary",
      initialNodes: [],
      initialLinks: [],
      rightSidebarSlot: <div>Playbook Settings</div>,
      onSave: async () => {},
    });

    expect(await screen.findByText(/Playbook Studio/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /publish/i })).not.toBeInTheDocument();
    expect(screen.getByText("Playbook Settings")).toBeInTheDocument();
    expect(screen.getByText("Diagram Summary")).toBeInTheDocument();
    expect(screen.getByText("playbook summary")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /collapse header details panel/i })).toBeInTheDocument();
    expect(screen.getByText("playbook summary")).toHaveClass("text-[9px]");
  });

  it("collapses everything below the header divider", async () => {
    await renderStudioFullCanvas({
      entityType: "workflow",
      entityId: "workflow-1",
      entityName: "Workflow One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      rightSidebarSlot: <div>Workflow Settings</div>,
      onSave: async () => {},
      onPublish: async () => {},
    });

    expect(screen.getByText("Workflow Settings")).toBeInTheDocument();
    expect(screen.getByText("Diagram Summary")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /collapse header details panel/i }));

    expect(screen.queryByText("Workflow Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Diagram Summary")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand header details panel/i })).toBeInTheDocument();
  });

  it("keeps the expand control at the far right of the studio title row", async () => {
    await renderStudioFullCanvas({
      entityType: "playbook",
      entityId: "playbook-1",
      entityName: "Ops Playbook",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      rightSidebarSlot: <div>Playbook Settings</div>,
      onSave: async () => {},
    });

    const collapseButton = screen.getByRole("button", { name: /collapse header details panel/i });
    const titleText = screen.getByText(/Playbook Studio \/ Ops Playbook/i);
    const titleRow = collapseButton.parentElement;

    expect(titleRow).not.toBeNull();
    expect(titleRow).toContainElement(collapseButton);
    expect(titleRow).toContainElement(titleText);
    expect(titleRow?.lastElementChild).toBe(collapseButton);
  });

  it("saves and returns when a return action is configured", async () => {
    const onSave = vi.fn(async () => {});

    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave,
      onPublish: async () => {},
      returnAction: { label: "Save & Return", href: "/library/workflows/wf-1" },
    });

    fireEvent.click(screen.getByRole("button", { name: /save & return/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(routerPushSpy).toHaveBeenCalledWith("/library/workflows/wf-1"));
  });

  it("does not navigate when save and return fails", async () => {
    const onSave = vi.fn(async () => {
      throw new Error("save failed");
    });

    await renderStudioFullCanvas({
      entityType: "agent",
      entityId: "agent-1",
      entityName: "Agent One",
      description: "desc",
      initialNodes: [],
      initialLinks: [],
      onSave,
      onPublish: async () => {},
      returnAction: { label: "Save & Return", href: "/library/workflows/wf-1" },
    });

    fireEvent.click(screen.getByRole("button", { name: /save & return/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(routerPushSpy).not.toHaveBeenCalled();
  });
});
