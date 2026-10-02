import {
  AgentDefinition,
  ArtifactSummary,
  GuardrailRuleSet,
  InboxItem,
  WorkflowDefinition,
  WorkflowRunEvent,
  WorkflowRunSummary,
} from "@/types/frontier";

export const mockRuns: WorkflowRunSummary[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    title: "Investor Pack — Andreessen Horowitz — Jane Doe",
    status: "Running",
    updatedAt: "2m ago",
    progressLabel: "Step 3/6",
    kind: "workflow",
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    title: "Design Partner Outreach — Fintech Segment",
    status: "Needs Review",
    updatedAt: "12m ago",
    progressLabel: "Step 5/6",
    kind: "workflow",
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    title: "Prospect Outreach — Federal Integrators",
    status: "Done",
    updatedAt: "1h ago",
    progressLabel: "Step 6/6",
    kind: "task",
  },
];

export const mockEvents: WorkflowRunEvent[] = [
  {
    id: "evt-1",
    type: "user_message",
    title: "Intake",
    summary: "Target enterprise design partners in regulated sectors.",
    createdAt: "09:11",
  },
  {
    id: "evt-2",
    type: "agent_message",
    title: "Plan",
    summary:
      "1) Research target profiles 2) Synthesize personas 3) Draft outreach pack 4) Critic pass 5) Guardrails check.",
    createdAt: "09:12",
  },
  {
    id: "evt-3",
    type: "step_completed",
    title: "Research complete",
    summary: "Collected firmographic and strategic signals for 24 targets.",
    createdAt: "09:15",
  },
  {
    id: "evt-4",
    type: "artifact_created",
    title: "Draft generated",
    summary: "Created investor outreach draft v2 and call brief artifact.",
    createdAt: "09:18",
  },
  {
    id: "evt-5",
    type: "guardrail_result",
    title: "Guardrails",
    summary: "1 warning: unverifiable performance claim in paragraph 3.",
    createdAt: "09:19",
  },
  {
    id: "evt-6",
    type: "approval_required",
    title: "Needs approval",
    summary: "Send/export action gated until artifact approval.",
    createdAt: "09:20",
  },
];

export const mockArtifacts: ArtifactSummary[] = [
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", name: "Investor Brief", status: "Needs Review", version: 2 },
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2", name: "Email Sequence", status: "Draft", version: 1 },
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3", name: "Call Prep Sheet", status: "Approved", version: 3 },
];

export const mockInbox: InboxItem[] = [
  {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
    runId: "22222222-2222-4222-8222-222222222222",
    runName: "Design Partner Outreach — Fintech Segment",
    artifactType: "Email Sequence",
    reason: "Draft produced and awaiting review",
    queue: "Needs Review",
  },
  {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
    runId: "11111111-1111-4111-8111-111111111111",
    runName: "Investor Pack — Andreessen Horowitz — Jane Doe",
    artifactType: "Investor Brief",
    reason: "Approval required before export",
    queue: "Needs Approval",
  },
  {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3",
    runId: "11111111-1111-4111-8111-111111111111",
    runName: "Investor Pack — Andreessen Horowitz — Jane Doe",
    artifactType: "Run Intake",
    reason: "Missing ideal account size threshold",
    queue: "Clarifications Requested",
  },
];

export const mockPublishedWorkflows: WorkflowDefinition[] = [
  {
    id: "44444444-4444-4444-8444-444444444444",
    name: "Investor Outreach Pack",
    description: "Research, draft, guardrails, and approval-gated outreach workflow.",
    version: 4,
    status: "published",
  },
  {
    id: "55555555-5555-4555-8555-555555555555",
    name: "Design Partner Outreach Pack",
    description: "Persona synthesis and multi-touch engagement sequence for pilot targets.",
    version: 2,
    status: "published",
  },
  {
    id: "66666666-6666-4666-8666-666666666666",
    name: "Prospect Outreach Pack",
    description: "Prospect qualification and outbound drafting for account-based motions.",
    version: 3,
    status: "published",
  },
];

export const mockWorkflowDefinitions: WorkflowDefinition[] = [
  ...mockPublishedWorkflows,
  {
    id: "77777777-7777-4777-8777-777777777777",
    name: "Enterprise RFP Response",
    description: "Draft and validate RFP responses with compliance checks.",
    version: 1,
    status: "draft",
  },
];

export const mockAgentDefinitions: AgentDefinition[] = [
  { id: "88888888-8888-4888-8888-888888888888", name: "Orchestration Agent", version: 5, status: "published", type: "form" },
  { id: "99999999-9999-4999-8999-999999999999", name: "Market Intelligence Agent", version: 4, status: "published", type: "form" },
  { id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", name: "Outreach Critic", version: 2, status: "draft", type: "form" },
];

export const mockGuardrailRulesets: GuardrailRuleSet[] = [
  { id: "12121212-1212-4121-8121-121212121212", name: "Core Messaging Guardrails", version: 3, status: "published" },
  { id: "34343434-3434-4343-8343-343434343434", name: "Regulated Industry Claims", version: 1, status: "draft" },
];
