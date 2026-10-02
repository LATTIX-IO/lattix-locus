export type WidgetSpec = {
  key: string;
  label: string;
  kind: "text" | "number" | "combo" | "toggle" | "list";
  defaultValue: string | number | boolean | string[];
  options?: string[];
  multiline?: boolean;
  help?: string;
  placeholder?: string;
};

export type PortSpec = {
  name: string;
  type: string;
};

type NodeSchema = {
  inputs: PortSpec[];
  outputs: PortSpec[];
  defaults: Record<string, unknown>;
  widgets: WidgetSpec[];
  inputAliases?: Record<string, string>;
  outputAliases?: Record<string, string>;
};

function normalizeBaseNodeType(type: string): string {
  if (type.startsWith("frontier/")) {
    return type.slice("frontier/".length);
  }
  if (type.startsWith("agent/")) {
    return "agent";
  }
  return type;
}

const SCHEMAS: Record<string, NodeSchema> = {
  trigger: {
    inputs: [{ name: "in", type: "flow" }],
    outputs: [
      { name: "out", type: "flow" },
      { name: "payload", type: "data" },
    ],
    defaults: {
      trigger_mode: "manual",
      schedule_preset: "daily",
      schedule_time: "09:00",
      schedule_day_of_week: "1",
      schedule_day_of_month: "1",
      default_message: "",
      schedule_cron: "0 9 * * 1-5",
      schedule_timezone: "UTC",
      webhook_path: "/hooks/frontier/trigger",
      webhook_secret_ref: "",
      api_event_name: "event.workflow.start",
      tool_event_name: "tool.completed",
      human_queue: "needs-review",
      tags: [],
    },
    widgets: [
      {
        key: "trigger_mode",
        label: "trigger_mode",
        kind: "combo",
        defaultValue: "manual",
        options: ["manual", "schedule", "webhook", "api_event", "tool_event", "human_feedback"],
        help: "How this workflow starts. Manual = click run. Schedule = time-based. Webhook/API/tool/human modes trigger from external events.",
      },
      {
        key: "schedule_preset",
        label: "schedule_preset",
        kind: "combo",
        defaultValue: "daily",
        options: ["hourly", "daily", "weekdays", "weekends", "weekly", "monthly", "custom"],
        help: "Human-friendly schedule choice. Use custom only when you need an advanced CRON expression.",
      },
      {
        key: "schedule_time",
        label: "schedule_time (HH:MM)",
        kind: "text",
        defaultValue: "09:00",
        placeholder: "09:00",
        help: "Local time used by non-hourly presets (24-hour format). Example: 17:30.",
      },
      {
        key: "schedule_day_of_week",
        label: "schedule_day_of_week",
        kind: "combo",
        defaultValue: "1",
        options: ["0", "1", "2", "3", "4", "5", "6"],
        help: "Used by weekly preset. 0=Sunday, 1=Monday ... 6=Saturday.",
      },
      {
        key: "schedule_day_of_month",
        label: "schedule_day_of_month",
        kind: "combo",
        defaultValue: "1",
        options: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28"],
        help: "Used by monthly preset. Pick day 1-28 for consistent monthly schedules.",
      },
      {
        key: "schedule_cron",
        label: "schedule_cron",
        kind: "text",
        defaultValue: "0 9 * * 1-5",
        placeholder: "0 9 * * 1-5",
        help: "Advanced fallback schedule format. Prefer schedule presets unless you need custom expressions.",
      },
      {
        key: "schedule_timezone",
        label: "schedule_timezone",
        kind: "combo",
        defaultValue: "UTC",
        options: ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London"],
        help: "Timezone used when translating schedule presets/time into runtime schedule behavior.",
      },
      {
        key: "webhook_path",
        label: "webhook_path",
        kind: "text",
        defaultValue: "/hooks/frontier/trigger",
        help: "Path endpoint for webhook trigger mode.",
      },
      {
        key: "webhook_secret_ref",
        label: "webhook_secret_ref",
        kind: "text",
        defaultValue: "",
        help: "Secret reference used to verify webhook signatures.",
      },
      {
        key: "api_event_name",
        label: "api_event_name",
        kind: "text",
        defaultValue: "event.workflow.start",
        help: "Event key listened to in api_event mode.",
      },
      {
        key: "tool_event_name",
        label: "tool_event_name",
        kind: "text",
        defaultValue: "tool.completed",
        help: "Event name listened to in tool_event mode.",
      },
      {
        key: "human_queue",
        label: "human_queue",
        kind: "text",
        defaultValue: "needs-review",
        help: "Queue/channel to listen to in human_feedback mode.",
      },
    ],
    outputAliases: { output: "out", message: "payload" },
  },
  prompt: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "prompt", type: "prompt" },
    ],
    defaults: {
      objective: "general_assistant",
      style: "concise",
      audience: "technical",
      safety_level: "balanced",
      include_citations: false,
      system_prompt_text: "",
    },
    widgets: [
      {
        key: "objective",
        label: "objective",
        kind: "combo",
        defaultValue: "general_assistant",
        options: ["general_assistant", "agent_execution", "research", "planning", "compliance", "summarization", "code_review", "incident_response", "sales_support", "knowledge_extraction", "creative_writing"],
        help: "Primary outcome this system prompt should optimize. Used to shape planning and response framing.",
      },
      {
        key: "style",
        label: "style",
        kind: "combo",
        defaultValue: "concise",
        options: ["concise", "detailed", "executive", "step_by_step", "socratic", "instructional", "evidence_based", "policy_aligned"],
        help: "Communication style only. Example: policy_aligned means wording aligns to policy language, but does NOT replace guardrail enforcement.",
      },
      {
        key: "audience",
        label: "audience",
        kind: "combo",
        defaultValue: "technical",
        options: ["operator", "technical", "executive", "customer", "analyst", "developer", "security", "compliance", "legal", "sales", "finance", "hr", "general_public"],
        help: "Intended reader persona. This controls wording depth, jargon level, and output framing.",
      },
      {
        key: "safety_level",
        label: "safety_level",
        kind: "combo",
        defaultValue: "balanced",
        options: ["very_strict", "strict", "high", "balanced", "moderate", "permissive"],
        help: "Prompt steering strictness. This influences generation behavior only; guardrail node still performs enforceable policy checks.",
      },
      {
        key: "include_citations",
        label: "include_citations",
        kind: "toggle",
        defaultValue: false,
        help: "When enabled, asks model to add source/citation notes when factual claims are made.",
      },
      {
        key: "system_prompt_text",
        label: "system_prompt_text",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Primary system prompt content fed into downstream agent runtime via the prompt port.",
      },
    ],
    inputAliases: { data: "context" },
    outputAliases: { output: "prompt", system_prompt: "prompt" },
  },
  goal: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "goal", type: "goal" },
    ],
    defaults: {
      intent: "",
      success_criteria: [],
      constraints: [],
      priorities: [],
      output_contract: "",
    },
    widgets: [
      {
        key: "intent",
        label: "intent",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Primary task objective that the assembly should optimize for.",
      },
      {
        key: "success_criteria",
        label: "success_criteria",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "One item per line or JSON list describing what success looks like.",
      },
      {
        key: "constraints",
        label: "constraints",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Explicit constraints or guard boundaries the cognitive flow must respect.",
      },
      {
        key: "priorities",
        label: "priorities",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Ordered priorities that help break ties during synthesis and commitment.",
      },
      {
        key: "output_contract",
        label: "output_contract",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Expected decision/output format the commitment should produce.",
      },
    ],
    outputAliases: { output: "goal", belief: "goal" },
  },
  evidence: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "evidence", type: "evidence" },
    ],
    defaults: {
      allowed_sources: [],
      required_evidence: [],
      ranking_rules: "freshness_then_relevance",
    },
    widgets: [
      {
        key: "allowed_sources",
        label: "allowed_sources",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Allowed evidence sources for this node (one per line or JSON list).",
      },
      {
        key: "required_evidence",
        label: "required_evidence",
        kind: "text",
        defaultValue: "",
        multiline: true,
        help: "Required evidence phrases or categories that must be present before commitment.",
      },
      {
        key: "ranking_rules",
        label: "ranking_rules",
        kind: "combo",
        defaultValue: "freshness_then_relevance",
        options: ["freshness_then_relevance", "relevance_then_freshness", "manual"],
        help: "How evidence should be prioritized before assembly.",
      },
    ],
    outputAliases: { output: "evidence", claims: "evidence" },
  },
  agent: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "prompt", type: "prompt" },
      { name: "guardrail", type: "guardrail" },
      { name: "memory", type: "memory" },
      { name: "context", type: "data" },
      { name: "retrieval", type: "retrieval" },
      { name: "tool_result", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "response", type: "data" },
      { name: "retrieval_query", type: "data" },
      { name: "tool_request", type: "tool" },
      { name: "state_delta", type: "data" },
      { name: "memory", type: "memory" },
      { name: "guardrail", type: "guardrail" },
    ],
    defaults: {
      agent_id: "",
      role: "",
      model: "ollama/gpt-oss:20b",
      temperature: 0.2,
      max_steps: 8,
      timeout_ms: 45000,
      execution_mode: "sync",
      harness_mode: "chat",
      system_prompt: "",
      skills: [],
    },
    widgets: [
      { key: "agent_id", label: "agent_id", kind: "text", defaultValue: "", help: "Target agent identity to execute. Resolves to a seeded agent definition — uses that agent's real system prompt and model when run." },
      { key: "role", label: "role", kind: "combo", defaultValue: "general", options: ["general", "planner", "executor", "reviewer", "specialist"], help: "High-level runtime role hint used for execution behavior framing." },
      { key: "model", label: "model", kind: "combo", defaultValue: "ollama/gpt-oss:20b", options: ["ollama/gpt-oss:20b", "ollama/gpt-oss:120b", "gpt-5.2", "gpt-5.2-mini", "gpt-4.1-mini"], help: "Model used for generation. When agent_id resolves to a seeded agent, that agent's model_defaults take precedence at run time." },
      { key: "temperature", label: "temperature", kind: "number", defaultValue: 0.2, help: "Randomness control. Lower is more deterministic; higher is more creative." },
      { key: "max_steps", label: "max_steps", kind: "number", defaultValue: 8, help: "Bound on iterative reasoning/delegation steps for this runtime node." },
      { key: "timeout_ms", label: "timeout_ms", kind: "number", defaultValue: 45000, help: "Execution timeout per node run in milliseconds. Increase for long-running tasks." },
      { key: "execution_mode", label: "execution_mode", kind: "combo", defaultValue: "sync", options: ["sync", "async"], help: "sync waits for completion in-line; async is intended for background/deferred execution semantics." },
      { key: "harness_mode", label: "harness_mode", kind: "combo", defaultValue: "chat", options: ["chat", "code", "team"], help: "chat = single-shot reasoning; code = run the harness coding loop (real file edits + tests in the bound workspace); team = run the full cross-functional CollaborativeTeam." },
      { key: "system_prompt", label: "system_prompt", kind: "text", defaultValue: "", multiline: true, help: "Inline prompt fallback. If a prompt node is connected, connected prompt typically takes precedence depending on runtime composition order." },
      { key: "skills", label: "skills", kind: "list", defaultValue: [], options: [], help: "Optional slash skills to attach to this agent node. Enter one skill per line or use the scoped suggestions below." },
    ],
    outputAliases: { output: "out", tool_api: "tool_request", query: "retrieval_query", request: "tool_request" },
  },
  assembly: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "goal", type: "goal" },
      { name: "evidence", type: "evidence" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "synthesis", type: "data" },
      { name: "commitment", type: "commitment" },
      { name: "dissent", type: "data" },
    ],
    defaults: {
      consensus_policy: "weighted-support",
      inference_mode: "bounded",
      confidence_threshold: 0.6,
    },
    widgets: [
      {
        key: "consensus_policy",
        label: "consensus_policy",
        kind: "combo",
        defaultValue: "weighted-support",
        options: ["weighted-support"],
        help: "Consensus policy used to fuse goal and evidence in the MVP slice.",
      },
      {
        key: "inference_mode",
        label: "inference_mode",
        kind: "combo",
        defaultValue: "bounded",
        options: ["bounded"],
        help: "Inference strategy for the MVP assembly runtime.",
      },
      {
        key: "confidence_threshold",
        label: "confidence_threshold",
        kind: "number",
        defaultValue: 0.6,
        help: "Minimum confidence required before commitment is considered autonomous.",
      },
    ],
    outputAliases: { output: "commitment", proposal: "commitment" },
  },
  commitment: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "commitment", type: "commitment" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "result", type: "data" },
    ],
    defaults: {
      autonomy_level: "bounded",
      confidence_threshold: 0.6,
      escalation_rules: "human_checkpoint",
    },
    widgets: [
      {
        key: "autonomy_level",
        label: "autonomy_level",
        kind: "combo",
        defaultValue: "bounded",
        options: ["bounded", "manual"],
        help: "How much autonomy the commitment node is allowed to exercise.",
      },
      {
        key: "confidence_threshold",
        label: "confidence_threshold",
        kind: "number",
        defaultValue: 0.6,
        help: "Confidence threshold required before finalizing the commitment.",
      },
      {
        key: "escalation_rules",
        label: "escalation_rules",
        kind: "combo",
        defaultValue: "human_checkpoint",
        options: ["human_checkpoint", "manual_review"],
        help: "Escalation behavior when blockers or low confidence prevent commitment.",
      },
    ],
    inputAliases: { proposal: "commitment" },
    outputAliases: { output: "result", published: "result" },
  },
  workflow: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "payload", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "result", type: "data" },
    ],
    defaults: {
      workflow_id: "",
      handoff_mode: "blocking",
      entry_message: "",
      output_binding: "result",
    },
    widgets: [
      { key: "workflow_id", label: "workflow_id", kind: "combo", defaultValue: "", options: [], help: "Published or draft workflow definition to invoke from this playbook step." },
      { key: "handoff_mode", label: "handoff_mode", kind: "combo", defaultValue: "blocking", options: ["blocking", "fan_out", "fan_in"], help: "How this playbook step composes multiple workflow branches." },
      { key: "entry_message", label: "entry_message", kind: "text", defaultValue: "", help: "Optional kickoff message or instruction passed into the child workflow." },
      { key: "output_binding", label: "output_binding", kind: "combo", defaultValue: "result", options: ["result", "artifacts", "status"], help: "Which child workflow output should flow downstream in the playbook graph." },
    ],
    inputAliases: { data: "payload", request: "payload" },
    outputAliases: { output: "result", data: "result" },
  },
  "tool-call": {
    inputs: [
      { name: "in", type: "flow" },
      { name: "request", type: "tool" },
      { name: "auth_context", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "result", type: "data" },
      { name: "status", type: "data" },
      { name: "guardrail", type: "guardrail" },
    ],
    defaults: { tool_id: "tool/unspecified", method: "POST", timeout_ms: 30000, retry_count: 2 },
    widgets: [
      { key: "tool_id", label: "tool_id", kind: "combo", defaultValue: "tool/unspecified", options: ["tool/unspecified", "tool/search", "tool/http", "tool/retrieval", "tool/code", "tool/sql", "tool/file", "tool/email", "tool/slack", "tool/mcp"], help: "Tool or integration identifier to invoke." },
      { key: "method", label: "method", kind: "combo", defaultValue: "POST", options: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], help: "HTTP verb for API-style tools." },
      { key: "endpoint_url", label: "endpoint_url", kind: "text", defaultValue: "", help: "Target endpoint URL. Must pass egress allowlist policies at runtime." },
      { key: "auth_type", label: "auth_type", kind: "combo", defaultValue: "none", options: ["none", "api_key", "bearer", "oauth2", "basic", "mcp_token"], help: "Authentication strategy to apply for outbound call auth context." },
      { key: "auth_secret_ref", label: "auth_secret_ref", kind: "text", defaultValue: "", help: "Secret reference (not raw secret) used to resolve credentials at runtime." },
      { key: "input_schema", label: "input_schema", kind: "text", defaultValue: "", multiline: true, help: "Optional JSON schema or contract notes for expected input payload shape." },
      { key: "mcp_connection_id", label: "mcp_connection_id", kind: "combo", defaultValue: "", options: [], help: "Approved staged MCP connection to resolve at runtime instead of hand-entering a server URL." },
      { key: "mcp_server_url", label: "mcp_server_url", kind: "text", defaultValue: "", help: "MCP endpoint URL. Must be approved by platform allowed_mcp_server_urls." },
      { key: "mcp_tool_name", label: "mcp_tool_name", kind: "text", defaultValue: "", help: "Tool/function name exposed by the MCP server." },
      { key: "timeout_ms", label: "timeout_ms", kind: "number", defaultValue: 30000, help: "Max tool execution time in milliseconds." },
      { key: "retry_count", label: "retry_count", kind: "number", defaultValue: 2, help: "Retry attempts for transient failures." },
    ],
    inputAliases: { tool_input: "request", data: "context" },
    outputAliases: { output: "out", tool_output: "result" },
  },
  retrieval: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "query", type: "data" },
      { name: "filters", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "documents", type: "retrieval" },
      { name: "grounding_context", type: "data" },
    ],
    defaults: { source_type: "hybrid", top_k: 5, score_threshold: 0.2 },
    widgets: [
      { key: "source_type", label: "source_type", kind: "combo", defaultValue: "hybrid", options: ["vector", "keyword", "hybrid", "semantic", "graph", "sql", "api"], help: "Retrieval strategy/backend type. hybrid typically combines lexical + vector retrieval." },
      { key: "source_id", label: "source_id", kind: "text", defaultValue: "kb://default", help: "Registered retrieval source identifier (allowlisted on backend)." },
      { key: "source_url", label: "source_url", kind: "text", defaultValue: "", help: "Optional direct retrieval endpoint URL if your retrieval adapter is API-backed." },
      { key: "auth_type", label: "auth_type", kind: "combo", defaultValue: "none", options: ["none", "api_key", "bearer", "oauth2", "basic"], help: "Authentication strategy for retrieval endpoint access when needed." },
      { key: "auth_secret_ref", label: "auth_secret_ref", kind: "text", defaultValue: "", help: "Secret reference for retrieval auth credentials." },
      { key: "index_name", label: "index_name", kind: "text", defaultValue: "", help: "Index/collection/table name for retrieval engines (e.g., vector DB index)." },
      { key: "top_k", label: "top_k", kind: "number", defaultValue: 5, help: "Maximum number of documents/chunks returned by retrieval before reranking/post-filtering." },
      { key: "score_threshold", label: "score_threshold", kind: "number", defaultValue: 0.2, help: "Minimum relevance score needed to keep results." },
      { key: "embedding_model", label: "embedding_model", kind: "text", defaultValue: "", help: "Embedding model identifier for vector-based retrieval setups." },
    ],
    inputAliases: { data: "query", request: "query" },
    outputAliases: { output: "out", retrieval: "documents", data: "grounding_context" },
  },
  memory: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "read_query", type: "data" },
      { name: "write_payload", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "memory_state", type: "memory" },
      { name: "context", type: "data" },
    ],
    defaults: { action: "append", scope: "session", session_id: "" },
    widgets: [
      { key: "action", label: "action", kind: "combo", defaultValue: "append", options: ["append", "read", "clear"], help: "append writes new memory, read fetches memory, clear deletes memory for target scope." },
      { key: "scope", label: "scope", kind: "combo", defaultValue: "session", options: ["run", "session", "user", "playbook", "tenant", "agent", "workflow", "global"], help: "Partition used to resolve memory bucket identity and isolation." },
      { key: "session_id", label: "session_id", kind: "text", defaultValue: "", help: "Explicit session key for session-scoped memory. Supports variable expressions." },
      { key: "user_id", label: "user_id", kind: "text", defaultValue: "", help: "User identifier for user-scoped memory (e.g., var.currentUser)." },
      { key: "playbook_id", label: "playbook_id", kind: "text", defaultValue: "", help: "Playbook identifier for playbook-scoped memory when a larger operating motion owns the state." },
      { key: "tenant_id", label: "tenant_id", kind: "text", defaultValue: "", help: "Tenant identifier for tenant-scoped memory when compatibility with tenant isolation is required." },
      { key: "agent_id", label: "agent_id", kind: "text", defaultValue: "", help: "Agent identifier for agent-scoped memory to help agent-specific learning/context retention." },
      { key: "workflow_id", label: "workflow_id", kind: "text", defaultValue: "", help: "Workflow identifier for workflow-scoped memory contexts." },
      { key: "dimension_key", label: "dimension_key", kind: "text", defaultValue: "", help: "Optional custom bucket identity override for advanced segmentation." },
    ],
    inputAliases: { payload: "write_payload", data: "read_query", query: "read_query" },
    outputAliases: { output: "out", memory: "memory_state", data: "context" },
  },
  guardrail: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "candidate_output", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "approved_output", type: "data" },
      { name: "violations", type: "data" },
      { name: "decision", type: "guardrail" },
    ],
    defaults: {
      stage: "output",
      ruleset_id: "",
      tripwire_action: "reject_content",
      run_in_parallel: false,
      blocked_keywords: [],
      detect_secrets: true,
      reject_message: "Blocked by policy",
    },
    widgets: [
      { key: "stage", label: "stage", kind: "combo", defaultValue: "output", options: ["input", "output", "tool_input", "tool_output"], help: "When guardrail runs in execution lifecycle." },
      { key: "ruleset_id", label: "ruleset_id", kind: "combo", defaultValue: "", options: [], help: "Published guardrail ruleset selection from Guardrails builder." },
      { key: "tripwire_action", label: "tripwire_action", kind: "combo", defaultValue: "reject_content", options: ["allow", "reject_content", "raise_exception"], help: "Action to take when violations are detected." },
      { key: "run_in_parallel", label: "run_in_parallel", kind: "toggle", defaultValue: false, help: "Run asynchronously for input/tool-input styles where applicable." },
      { key: "detect_secrets", label: "detect_secrets", kind: "toggle", defaultValue: true, help: "Enable pattern checks for likely secrets in payload content." },
      { key: "reject_message", label: "reject_message", kind: "text", defaultValue: "Blocked by policy", help: "Replacement message when tripwire_action is reject_content." },
    ],
    inputAliases: { candidate: "candidate_output", data: "candidate_output" },
    outputAliases: { output: "out", approved: "approved_output", guardrail: "decision", flagged: "violations" },
  },
  "human-review": {
    inputs: [
      { name: "in", type: "flow" },
      { name: "candidate", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "approved", type: "data" },
      { name: "feedback", type: "data" },
    ],
    defaults: { reviewer_group: "", required_approvals: 1, sla_minutes: 120 },
    widgets: [
      { key: "reviewer_group", label: "reviewer_group", kind: "combo", defaultValue: "security", options: ["security", "legal", "ops", "compliance"], help: "Team/group required to review this branch output." },
      { key: "required_approvals", label: "required_approvals", kind: "number", defaultValue: 1, help: "Number of approvals required before continuing." },
      { key: "sla_minutes", label: "sla_minutes", kind: "number", defaultValue: 120, help: "SLA window for review completion in minutes." },
    ],
    outputAliases: { output: "out" },
  },
  manifold: {
    inputs: [
      { name: "in_a", type: "flow" },
      { name: "in_b", type: "flow" },
      { name: "in_c", type: "flow" },
      { name: "in_d", type: "flow" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "data", type: "data" },
    ],
    defaults: { logic_mode: "OR", min_required: 1 },
    widgets: [
      { key: "logic_mode", label: "logic_mode", kind: "combo", defaultValue: "OR", options: ["AND", "OR"], help: "How many incoming branches must pass before output propagates." },
      { key: "min_required", label: "min_required", kind: "number", defaultValue: 1, help: "Minimum number of active inbound sources required." },
    ],
    outputAliases: { output: "out" },
  },
  router: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "candidate", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "match_a", type: "flow" },
      { name: "match_b", type: "flow" },
      { name: "default", type: "flow" },
      { name: "decision", type: "data" },
      { name: "matched_payload", type: "data" },
    ],
    defaults: {
      router_mode: "rules",
      decision_key: "priority",
      route_match_a: "priority",
      route_match_b: "standard",
      default_route: "default",
      allow_multi_match: false,
      rules_json: JSON.stringify(
        [
          { route: "priority", key: "priority", operator: "eq", value: "high" },
          { route: "standard", key: "priority", operator: "eq", value: "normal" },
        ],
        null,
        2,
      ),
      keyword_map_json: JSON.stringify(
        [
          { route: "incident", keywords: ["sev1", "incident", "critical"] },
          { route: "review", keywords: ["approval", "review"] },
        ],
        null,
        2,
      ),
      threshold_value: 0,
      threshold_operator: "gte",
    },
    widgets: [
      { key: "router_mode", label: "router_mode", kind: "combo", defaultValue: "rules", options: ["rules", "classifier", "threshold", "expression"], help: "Decision strategy used to select a route key from the input payload." },
      { key: "decision_key", label: "decision_key", kind: "text", defaultValue: "priority", help: "Payload field path inspected by rule, threshold, or expression modes." },
      { key: "route_match_a", label: "route_match_a", kind: "text", defaultValue: "priority", help: "Route key that activates the match_a flow output." },
      { key: "route_match_b", label: "route_match_b", kind: "text", defaultValue: "standard", help: "Route key that activates the match_b flow output." },
      { key: "default_route", label: "default_route", kind: "text", defaultValue: "default", help: "Fallback route key emitted when no rule or classifier match is found." },
      { key: "allow_multi_match", label: "allow_multi_match", kind: "toggle", defaultValue: false, help: "When enabled, retains every matching route rather than only the first match." },
      { key: "rules_json", label: "rules_json", kind: "text", defaultValue: JSON.stringify([{ route: "priority", key: "priority", operator: "eq", value: "high" }], null, 2), multiline: true, help: "JSON array of deterministic rule objects: route, key, operator, and value/contains/exists." },
      { key: "keyword_map_json", label: "keyword_map_json", kind: "text", defaultValue: JSON.stringify([{ route: "incident", keywords: ["sev1", "incident"] }], null, 2), multiline: true, help: "JSON array used by classifier mode to map keywords to route names." },
      { key: "threshold_value", label: "threshold_value", kind: "number", defaultValue: 0, help: "Numeric threshold used by threshold router mode." },
      { key: "threshold_operator", label: "threshold_operator", kind: "combo", defaultValue: "gte", options: ["gt", "gte", "lt", "lte", "eq"], help: "Comparison applied against decision_key for threshold mode." },
    ],
    inputAliases: { data: "candidate", payload: "candidate" },
    outputAliases: { output: "out", route: "decision", data: "decision" },
  },
  iterator: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "items", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "loop", type: "flow" },
      { name: "done", type: "flow" },
      { name: "item", type: "data" },
      { name: "aggregate", type: "data" },
    ],
    defaults: {
      iteration_mode: "foreach",
      item_path: "items",
      batch_size: 25,
      max_items: 100,
      aggregate_mode: "list",
    },
    widgets: [
      { key: "iteration_mode", label: "iteration_mode", kind: "combo", defaultValue: "foreach", options: ["foreach", "batch", "chunk", "paginate"], help: "Determines how the inbound list is chunked and which branch is activated." },
      { key: "item_path", label: "item_path", kind: "text", defaultValue: "items", help: "Dot-path used to locate the iterable list when the inbound payload is an object." },
      { key: "batch_size", label: "batch_size", kind: "number", defaultValue: 25, help: "Batch or chunk size used by batch and chunk iteration modes." },
      { key: "max_items", label: "max_items", kind: "number", defaultValue: 100, help: "Maximum number of items consumed from the iterable payload for a single node execution." },
      { key: "aggregate_mode", label: "aggregate_mode", kind: "combo", defaultValue: "list", options: ["list", "count", "first", "last"], help: "How the aggregate output is shaped for downstream nodes." },
    ],
    inputAliases: { data: "items", payload: "items" },
    outputAliases: { output: "out", data: "aggregate", result: "aggregate" },
  },
  transform: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "source", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "result", type: "data" },
    ],
    defaults: {
      transform_mode: "map",
      strict_validation: false,
      mapping_json: JSON.stringify(
        {
          priority: "{{var.source.priority}}",
          summary: "{{var.source.summary}}",
        },
        null,
        2,
      ),
      template_text: "Ticket {{var.source.id}} for {{var.source.owner}}",
      extract_path: "payload",
      redact_fields: "token,password,secret",
      output_schema: "",
    },
    widgets: [
      { key: "transform_mode", label: "transform_mode", kind: "combo", defaultValue: "map", options: ["map", "template", "extract", "redact", "merge"], help: "Deterministic data shaping mode executed locally in the runtime." },
      { key: "mapping_json", label: "mapping_json", kind: "text", defaultValue: JSON.stringify({ priority: "{{var.source.priority}}" }, null, 2), multiline: true, help: "JSON object used by map mode. Values may reference var.source and var.context." },
      { key: "template_text", label: "template_text", kind: "text", defaultValue: "Ticket {{var.source.id}}", multiline: true, help: "Text template for template mode. Template variables resolve against var.source and var.context." },
      { key: "extract_path", label: "extract_path", kind: "text", defaultValue: "payload", help: "Dot-path used by extract mode to pick a nested field from the source payload." },
      { key: "redact_fields", label: "redact_fields", kind: "text", defaultValue: "token,password,secret", help: "Comma-separated field names removed or masked during redact mode." },
      { key: "output_schema", label: "output_schema", kind: "text", defaultValue: "", multiline: true, help: "Optional JSON schema note or downstream contract documentation for the transform result." },
      { key: "strict_validation", label: "strict_validation", kind: "toggle", defaultValue: false, help: "When enabled, missing extract paths or malformed mapping JSON are surfaced as transform errors." },
    ],
    inputAliases: { data: "source", payload: "source" },
    outputAliases: { output: "out", data: "result" },
  },
  event: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "payload", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "resume", type: "flow" },
      { name: "idle", type: "flow" },
      { name: "event", type: "data" },
      { name: "receipt", type: "data" },
    ],
    defaults: {
      event_mode: "publish",
      topic: "frontier.events.default",
      event_name: "event.workflow.step",
      correlation_key: "runId",
      durable: false,
    },
    widgets: [
      { key: "event_mode", label: "event_mode", kind: "combo", defaultValue: "publish", options: ["publish", "consume", "resume"], help: "publish emits a new event envelope, consume reads the last matching event, resume emits a resume receipt." },
      { key: "topic", label: "topic", kind: "text", defaultValue: "frontier.events.default", help: "Logical event topic or queue name used by this node." },
      { key: "event_name", label: "event_name", kind: "text", defaultValue: "event.workflow.step", help: "Event name included in emitted or matched event envelopes." },
      { key: "correlation_key", label: "correlation_key", kind: "text", defaultValue: "runId", help: "Runtime variable or payload key used to correlate event envelopes." },
      { key: "durable", label: "durable", kind: "toggle", defaultValue: false, help: "Marks the emitted event as durable for external consumers." },
    ],
    inputAliases: { data: "payload", result: "payload" },
    outputAliases: { output: "out", data: "event" },
  },
  "data-store": {
    inputs: [
      { name: "in", type: "flow" },
      { name: "record", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "result", type: "data" },
      { name: "status", type: "data" },
    ],
    defaults: {
      operation: "upsert",
      store_scope: "session",
      collection: "default",
      record_key: "id",
      merge_strategy: "replace",
    },
    widgets: [
      { key: "operation", label: "operation", kind: "combo", defaultValue: "upsert", options: ["create", "read", "upsert", "append", "delete"], help: "Business record action performed inside the scoped data store." },
      { key: "store_scope", label: "store_scope", kind: "combo", defaultValue: "session", options: ["run", "session", "workflow", "playbook", "tenant"], help: "Namespace boundary for persisted records inside the graph runtime." },
      { key: "collection", label: "collection", kind: "text", defaultValue: "default", help: "Logical table or collection name used to group stored records." },
      { key: "record_key", label: "record_key", kind: "text", defaultValue: "id", help: "Primary key path read from the inbound record payload." },
      { key: "merge_strategy", label: "merge_strategy", kind: "combo", defaultValue: "replace", options: ["replace", "merge"], help: "How upsert merges with an existing record when one already exists." },
    ],
    inputAliases: { data: "record", payload: "record", result: "record" },
    outputAliases: { output: "out", data: "result" },
  },
  "error-handler": {
    inputs: [
      { name: "in", type: "flow" },
      { name: "error", type: "data" },
      { name: "context", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "handled", type: "data" },
      { name: "status", type: "data" },
    ],
    defaults: {
      handler_mode: "fallback",
      fallback_value: "{}",
      fallback_message: "Recovered from upstream failure.",
      retryable: false,
      error_key: "message",
      emit_status: true,
    },
    widgets: [
      { key: "handler_mode", label: "handler_mode", kind: "combo", defaultValue: "fallback", options: ["fallback", "normalize", "escalate"], help: "fallback substitutes payloads, normalize standardizes error shape, escalate marks the failure for downstream handling." },
      { key: "fallback_value", label: "fallback_value", kind: "text", defaultValue: "{}", multiline: true, help: "JSON payload or plain text returned when fallback mode handles an upstream failure." },
      { key: "fallback_message", label: "fallback_message", kind: "text", defaultValue: "Recovered from upstream failure.", help: "Human-readable recovery note included in handled output and status metadata." },
      { key: "retryable", label: "retryable", kind: "toggle", defaultValue: false, help: "Marks the normalized error as retryable for downstream decision logic." },
      { key: "error_key", label: "error_key", kind: "text", defaultValue: "message", help: "Primary key used when extracting a message from inbound error payloads." },
      { key: "emit_status", label: "emit_status", kind: "toggle", defaultValue: true, help: "Emit structured recovery status metadata on the status output." },
    ],
    inputAliases: { data: "error", result: "error", payload: "error" },
    outputAliases: { output: "out", data: "handled" },
  },
  wait: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "resume_payload", type: "data" },
    ],
    outputs: [
      { name: "out", type: "flow" },
      { name: "resume", type: "flow" },
      { name: "timeout", type: "flow" },
      { name: "result", type: "data" },
    ],
    defaults: {
      wait_mode: "delay",
      delay_ms: 1000,
      timeout_ms: 0,
      simulate_wait: true,
    },
    widgets: [
      { key: "wait_mode", label: "wait_mode", kind: "combo", defaultValue: "delay", options: ["delay", "timeout_gate", "resume_window"], help: "delay pauses briefly, timeout_gate marks timeout branches, resume_window emits resume metadata for later continuation." },
      { key: "delay_ms", label: "delay_ms", kind: "number", defaultValue: 1000, help: "Requested delay in milliseconds before the node emits a resume or timeout branch." },
      { key: "timeout_ms", label: "timeout_ms", kind: "number", defaultValue: 0, help: "If non-zero and below delay_ms, the timeout branch becomes active instead of resume." },
      { key: "simulate_wait", label: "simulate_wait", kind: "toggle", defaultValue: true, help: "When enabled, the node reports wait metadata without sleeping for the full delay." },
    ],
    inputAliases: { data: "resume_payload", payload: "resume_payload" },
    outputAliases: { output: "out", data: "result" },
  },
  output: {
    inputs: [
      { name: "in", type: "flow" },
      { name: "result", type: "data" },
    ],
    outputs: [{ name: "out", type: "flow" }],
    defaults: { destination: "artifact_store", format: "json" },
    widgets: [
      { key: "destination", label: "destination", kind: "combo", defaultValue: "artifact_store", options: ["artifact_store", "webhook", "email", "queue"], help: "Final publication target for run output." },
      { key: "format", label: "format", kind: "combo", defaultValue: "json", options: ["json", "markdown", "text"], help: "Output serialization format." },
    ],
    inputAliases: { "in-flow": "in", data: "result", approved: "result", approved_output: "result", payload: "result" },
  },
};

function schemaFor(type: string): NodeSchema {
  const base = normalizeBaseNodeType(type);
  return SCHEMAS[base] ?? {
    inputs: [{ name: "in", type: "flow" }],
    outputs: [{ name: "out", type: "flow" }],
    defaults: {},
    widgets: [],
  };
}

export function getNodePorts(type: string): { inputs: PortSpec[]; outputs: PortSpec[] } {
  const schema = schemaFor(type);
  return {
    inputs: schema.inputs,
    outputs: schema.outputs,
  };
}

export function getNodeDefaultConfig(type: string): Record<string, unknown> {
  const base = normalizeBaseNodeType(type);
  const schema = schemaFor(type);
  const defaults = { ...schema.defaults };
  if (base === "agent" && type.startsWith("agent/")) {
    const specificAgentId = type.split("/")[1] || "";
    defaults.agent_id = specificAgentId;
  }
  return defaults;
}

export function getNodeWidgets(type: string): WidgetSpec[] {
  const base = normalizeBaseNodeType(type);
  const schema = schemaFor(type);
  if (base === "agent" && type.startsWith("agent/")) {
    const specificAgentId = type.split("/")[1] || "";
    return schema.widgets.map((widget) => {
      if (widget.key === "agent_id") {
        return {
          ...widget,
          kind: "combo",
          defaultValue: specificAgentId || "current",
          options: specificAgentId ? [specificAgentId, "current"] : ["current"],
        };
      }
      return widget;
    });
  }
  return schema.widgets;
}

export function normalizeNodeTypeForSchema(type: string): string {
  return normalizeBaseNodeType(type);
}

export function resolveNodePortAlias(
  type: string,
  direction: "input" | "output",
  handleName?: string | null,
): string | null {
  const schema = schemaFor(type);
  const fallback = direction === "input" ? schema.inputs[0]?.name : schema.outputs[0]?.name;
  const original = (handleName || "").trim() || fallback || null;
  if (!original) {
    return null;
  }

  const aliases = direction === "input" ? schema.inputAliases : schema.outputAliases;
  if (aliases && aliases[original]) {
    return aliases[original];
  }

  const candidates = direction === "input" ? schema.inputs : schema.outputs;
  if (candidates.some((port) => port.name === original)) {
    return original;
  }

  if (aliases && aliases[original.toLowerCase()]) {
    return aliases[original.toLowerCase()];
  }

  return fallback || original;
}
