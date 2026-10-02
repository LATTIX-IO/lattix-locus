export type ConfigFieldType = "text" | "textarea" | "number" | "boolean" | "select" | "json" | "tags";

export type ConfigField = {
  key: string;
  label: string;
  type: ConfigFieldType;
  placeholder?: string;
  help?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: Array<{ label: string; value: string }>;
  defaultValue?: string | number | boolean;
};

export type NodeConfigSchema = {
  title: string;
  description: string;
  fields: ConfigField[];
};

const SCHEMAS: Record<string, NodeConfigSchema> = {
  trigger: {
    title: "Trigger Settings",
    description: "Control workflow kickoff mode and initial input shaping.",
    fields: [
      {
        key: "trigger_mode",
        label: "Trigger Mode",
        type: "select",
        options: [
          { label: "Manual", value: "manual" },
          { label: "Schedule", value: "schedule" },
          { label: "Webhook", value: "webhook" },
          { label: "Event", value: "event" },
        ],
        defaultValue: "manual",
      },
      {
        key: "default_message",
        label: "Default Message",
        type: "textarea",
        placeholder: "Initial task message when input is empty",
      },
      {
        key: "tags",
        label: "Run Tags",
        type: "tags",
        placeholder: "priority:high,tenant:acme",
      },
    ],
  },
  agent: {
    title: "Agent Settings",
    description: "Configure model behavior, prompting, tools, memory, and delegation targets.",
    fields: [
      { key: "agent_id", label: "Agent ID", type: "text", placeholder: "uuid or catalog id" },
      { key: "role", label: "Role", type: "text", placeholder: "Researcher, Planner, Writer" },
      {
        key: "goal",
        label: "Goal",
        type: "textarea",
        placeholder: "What this agent should accomplish in the workflow",
      },
      {
        key: "system_prompt",
        label: "System Prompt",
        type: "textarea",
        placeholder: "You are a specialist agent...",
      },
      { key: "model", label: "Model", type: "text", placeholder: "gpt-5.2" },
      { key: "temperature", label: "Temperature", type: "number", min: 0, max: 2, step: 0.1, defaultValue: 0.2 },
      { key: "top_p", label: "Top P", type: "number", min: 0, max: 1, step: 0.05, defaultValue: 1 },
      { key: "max_tokens", label: "Max Tokens", type: "number", min: 64, max: 64000, step: 64, defaultValue: 1200 },
      { key: "max_steps", label: "Max Steps", type: "number", min: 1, max: 50, step: 1, defaultValue: 8 },
      { key: "timeout_ms", label: "Timeout (ms)", type: "number", min: 1000, max: 600000, step: 1000, defaultValue: 45000 },
      {
        key: "execution_mode",
        label: "Execution Mode",
        type: "select",
        options: [
          { label: "Synchronous", value: "sync" },
          { label: "Asynchronous", value: "async" },
        ],
        defaultValue: "sync",
      },
      { key: "tools", label: "Tools", type: "tags", placeholder: "crm.lookup,web.search,db.query" },
      {
        key: "memory_mode",
        label: "Memory Mode",
        type: "select",
        options: [
          { label: "Disabled", value: "off" },
          { label: "Read", value: "read" },
          { label: "Write", value: "write" },
          { label: "Read/Write", value: "read_write" },
        ],
        defaultValue: "read_write",
      },
      { key: "handoff_targets", label: "Handoff Targets", type: "tags", placeholder: "agent/planner,agent/reviewer" },
      {
        key: "metadata",
        label: "Metadata JSON",
        type: "json",
        placeholder: '{"department":"strategy","priority":"high"}',
      },
    ],
  },
  workflow: {
    title: "Workflow Step Settings",
    description: "Configure which saved workflow this playbook step invokes and how its result flows onward.",
    fields: [
      { key: "workflow_id", label: "Workflow ID", type: "text", placeholder: "workflow definition id" },
      {
        key: "handoff_mode",
        label: "Handoff Mode",
        type: "select",
        options: [
          { label: "Blocking", value: "blocking" },
          { label: "Fan Out", value: "fan_out" },
          { label: "Fan In", value: "fan_in" },
        ],
        defaultValue: "blocking",
      },
      { key: "entry_message", label: "Entry Message", type: "textarea", placeholder: "Instruction passed into the child workflow" },
      {
        key: "output_binding",
        label: "Output Binding",
        type: "select",
        options: [
          { label: "Result", value: "result" },
          { label: "Artifacts", value: "artifacts" },
          { label: "Status", value: "status" },
        ],
        defaultValue: "result",
      },
    ],
  },
  "tool-call": {
    title: "Tool / API Settings",
    description: "Configure tool invocation, retries, schema expectations, and tool guardrails.",
    fields: [
      { key: "tool_id", label: "Tool ID", type: "text", placeholder: "tool/unspecified" },
      { key: "endpoint", label: "Endpoint", type: "text", placeholder: "https://api.example.com/v1/action" },
      {
        key: "method",
        label: "HTTP Method",
        type: "select",
        options: [
          { label: "GET", value: "GET" },
          { label: "POST", value: "POST" },
          { label: "PUT", value: "PUT" },
          { label: "PATCH", value: "PATCH" },
          { label: "DELETE", value: "DELETE" },
        ],
        defaultValue: "POST",
      },
      { key: "timeout_ms", label: "Timeout (ms)", type: "number", min: 1000, max: 300000, step: 1000, defaultValue: 30000 },
      { key: "retry_count", label: "Retry Count", type: "number", min: 0, max: 10, step: 1, defaultValue: 2 },
      { key: "headers_json", label: "Headers JSON", type: "json", placeholder: '{"Authorization":"Bearer ..."}' },
      { key: "request_template", label: "Request Template", type: "json", placeholder: '{"query":"{{input}}"}' },
      {
        key: "expected_output_schema",
        label: "Expected Output Schema",
        type: "json",
        placeholder: '{"type":"object","properties":{"ok":{"type":"boolean"}}}',
      },
      {
        key: "tool_input_guardrail",
        label: "Tool Input Guardrail JSON",
        type: "json",
        placeholder: '{"tripwire_action":"reject_content","blocked_keywords":["secret"]}',
      },
      {
        key: "tool_output_guardrail",
        label: "Tool Output Guardrail JSON",
        type: "json",
        placeholder: '{"tripwire_action":"raise_exception","detect_secrets":true}',
      },
    ],
  },
  retrieval: {
    title: "Retrieval Settings",
    description: "Control retrieval source, ranking, and context assembly.",
    fields: [
      {
        key: "source_type",
        label: "Source Type",
        type: "select",
        options: [
          { label: "Vector", value: "vector" },
          { label: "Keyword", value: "keyword" },
          { label: "Hybrid", value: "hybrid" },
        ],
        defaultValue: "hybrid",
      },
      { key: "index", label: "Index / Collection", type: "text", placeholder: "knowledge-index" },
      { key: "top_k", label: "Top K", type: "number", min: 1, max: 100, step: 1, defaultValue: 5 },
      { key: "score_threshold", label: "Score Threshold", type: "number", min: 0, max: 1, step: 0.01, defaultValue: 0.2 },
      { key: "reranker", label: "Use Reranker", type: "boolean", defaultValue: true },
      { key: "filters_json", label: "Filters JSON", type: "json", placeholder: '{"tenant":"acme"}' },
    ],
  },
  memory: {
    title: "Memory Settings",
    description: "Configure scope, action, retention, and conflict behavior for memory state.",
    fields: [
      {
        key: "action",
        label: "Action",
        type: "select",
        options: [
          { label: "Append", value: "append" },
          { label: "Read", value: "read" },
          { label: "Clear", value: "clear" },
        ],
        defaultValue: "append",
      },
      {
        key: "scope",
        label: "Scope",
        type: "select",
        options: [
          { label: "Run", value: "run" },
          { label: "Session", value: "session" },
          { label: "User", value: "user" },
          { label: "Playbook", value: "playbook" },
        ],
        defaultValue: "session",
      },
      { key: "session_id", label: "Session ID", type: "text", placeholder: "agent:uuid or workflow:uuid" },
      { key: "ttl_minutes", label: "TTL (minutes)", type: "number", min: 1, max: 525600, step: 1, defaultValue: 1440 },
      {
        key: "conflict_policy",
        label: "Conflict Policy",
        type: "select",
        options: [
          { label: "Last Write Wins", value: "last_write_wins" },
          { label: "Merge", value: "merge" },
          { label: "Reject", value: "reject" },
        ],
        defaultValue: "last_write_wins",
      },
    ],
  },
  guardrail: {
    title: "Guardrail Settings",
    description: "Define tripwires, stage, and mitigation behavior for safety/compliance.",
    fields: [
      {
        key: "stage",
        label: "Stage",
        type: "select",
        options: [
          { label: "Input", value: "input" },
          { label: "Output", value: "output" },
          { label: "Tool Input", value: "tool_input" },
          { label: "Tool Output", value: "tool_output" },
        ],
        defaultValue: "output",
      },
      { key: "ruleset_id", label: "Ruleset ID", type: "text", placeholder: "guardrail-ruleset-uuid" },
      {
        key: "tripwire_action",
        label: "Tripwire Action",
        type: "select",
        options: [
          { label: "Allow", value: "allow" },
          { label: "Reject Content", value: "reject_content" },
          { label: "Raise Exception", value: "raise_exception" },
        ],
        defaultValue: "reject_content",
      },
      { key: "run_in_parallel", label: "Run In Parallel", type: "boolean", defaultValue: false },
      { key: "blocked_keywords", label: "Blocked Keywords", type: "tags", placeholder: "secret,credential,classified" },
      { key: "required_keywords", label: "Required Keywords", type: "tags", placeholder: "approved,reviewed" },
      { key: "detect_secrets", label: "Detect Secrets", type: "boolean", defaultValue: true },
      { key: "min_length", label: "Min Length", type: "number", min: 0, max: 20000, step: 1 },
      { key: "max_length", label: "Max Length", type: "number", min: 1, max: 200000, step: 1 },
      { key: "reject_message", label: "Reject Message", type: "textarea", placeholder: "Blocked by policy" },
    ],
  },
  "human-review": {
    title: "Human Review Settings",
    description: "Define approval policy, assignees, and escalation behavior.",
    fields: [
      { key: "reviewer_group", label: "Reviewer Group", type: "text", placeholder: "security-reviewers" },
      { key: "required_approvals", label: "Required Approvals", type: "number", min: 1, max: 10, step: 1, defaultValue: 1 },
      { key: "sla_minutes", label: "SLA (minutes)", type: "number", min: 1, max: 10080, step: 1, defaultValue: 120 },
      { key: "escalation_group", label: "Escalation Group", type: "text", placeholder: "engineering-managers" },
      {
        key: "approval_criteria",
        label: "Approval Criteria",
        type: "textarea",
        placeholder: "List what must be true before approval",
      },
    ],
  },
  router: {
    title: "Router Settings",
    description: "Choose deterministic routing logic and route metadata for downstream steps.",
    fields: [
      {
        key: "router_mode",
        label: "Router Mode",
        type: "select",
        options: [
          { label: "Rules", value: "rules" },
          { label: "Classifier", value: "classifier" },
          { label: "Threshold", value: "threshold" },
          { label: "Expression", value: "expression" },
        ],
        defaultValue: "rules",
      },
      { key: "decision_key", label: "Decision Key", type: "text", placeholder: "priority or payload.severity" },
      { key: "route_match_a", label: "Route Match A", type: "text", placeholder: "priority", defaultValue: "priority" },
      { key: "route_match_b", label: "Route Match B", type: "text", placeholder: "standard", defaultValue: "standard" },
      { key: "default_route", label: "Default Route", type: "text", placeholder: "default", defaultValue: "default" },
      { key: "allow_multi_match", label: "Allow Multiple Matches", type: "boolean", defaultValue: false },
      { key: "rules_json", label: "Rules JSON", type: "json", placeholder: '[{"route":"priority","key":"priority","operator":"eq","value":"high"}]' },
      { key: "keyword_map_json", label: "Keyword Map JSON", type: "json", placeholder: '[{"route":"incident","keywords":["sev1","critical"]}]' },
      { key: "threshold_value", label: "Threshold Value", type: "number", defaultValue: 0 },
      {
        key: "threshold_operator",
        label: "Threshold Operator",
        type: "select",
        options: [
          { label: ">", value: "gt" },
          { label: ">=", value: "gte" },
          { label: "<", value: "lt" },
          { label: "<=", value: "lte" },
          { label: "=", value: "eq" },
        ],
        defaultValue: "gte",
      },
    ],
  },
  iterator: {
    title: "Iterator Settings",
    description: "Process lists, batches, and paginated payloads with loop and done branches.",
    fields: [
      {
        key: "iteration_mode",
        label: "Iteration Mode",
        type: "select",
        options: [
          { label: "For Each", value: "foreach" },
          { label: "Batch", value: "batch" },
          { label: "Chunk", value: "chunk" },
          { label: "Paginate", value: "paginate" },
        ],
        defaultValue: "foreach",
      },
      { key: "item_path", label: "Item Path", type: "text", placeholder: "items", defaultValue: "items" },
      { key: "batch_size", label: "Batch Size", type: "number", defaultValue: 25 },
      { key: "max_items", label: "Max Items", type: "number", defaultValue: 100 },
      {
        key: "aggregate_mode",
        label: "Aggregate Mode",
        type: "select",
        options: [
          { label: "List", value: "list" },
          { label: "Count", value: "count" },
          { label: "First", value: "first" },
          { label: "Last", value: "last" },
        ],
        defaultValue: "list",
      },
    ],
  },
  transform: {
    title: "Transform Settings",
    description: "Perform deterministic data shaping locally without an LLM or external tool hop.",
    fields: [
      {
        key: "transform_mode",
        label: "Transform Mode",
        type: "select",
        options: [
          { label: "Map", value: "map" },
          { label: "Template", value: "template" },
          { label: "Extract", value: "extract" },
          { label: "Redact", value: "redact" },
          { label: "Merge", value: "merge" },
        ],
        defaultValue: "map",
      },
      { key: "mapping_json", label: "Mapping JSON", type: "json", placeholder: '{"priority":"{{var.source.priority}}"}' },
      { key: "template_text", label: "Template Text", type: "textarea", placeholder: 'Ticket {{var.source.id}} for {{var.source.owner}}' },
      { key: "extract_path", label: "Extract Path", type: "text", placeholder: "payload.details.summary" },
      { key: "redact_fields", label: "Redact Fields", type: "tags", placeholder: "token,password,secret" },
      { key: "output_schema", label: "Output Schema", type: "json", placeholder: '{"type":"object"}' },
      { key: "strict_validation", label: "Strict Validation", type: "boolean", defaultValue: false },
    ],
  },
  event: {
    title: "Event Settings",
    description: "Publish, consume, or resume event envelopes with receipts and correlation metadata.",
    fields: [
      {
        key: "event_mode",
        label: "Event Mode",
        type: "select",
        options: [
          { label: "Publish", value: "publish" },
          { label: "Consume", value: "consume" },
          { label: "Resume", value: "resume" },
        ],
        defaultValue: "publish",
      },
      { key: "topic", label: "Topic", type: "text", placeholder: "frontier.events.default", defaultValue: "frontier.events.default" },
      { key: "event_name", label: "Event Name", type: "text", placeholder: "event.workflow.step", defaultValue: "event.workflow.step" },
      { key: "correlation_key", label: "Correlation Key", type: "text", placeholder: "runId", defaultValue: "runId" },
      { key: "durable", label: "Durable", type: "boolean", defaultValue: false },
    ],
  },
  "data-store": {
    title: "Data Store Settings",
    description: "Persist and retrieve business records inside a scoped runtime data store.",
    fields: [
      {
        key: "operation",
        label: "Operation",
        type: "select",
        options: [
          { label: "Create", value: "create" },
          { label: "Read", value: "read" },
          { label: "Upsert", value: "upsert" },
          { label: "Append", value: "append" },
          { label: "Delete", value: "delete" },
        ],
        defaultValue: "upsert",
      },
      {
        key: "store_scope",
        label: "Store Scope",
        type: "select",
        options: [
          { label: "Run", value: "run" },
          { label: "Session", value: "session" },
          { label: "Workflow", value: "workflow" },
          { label: "Playbook", value: "playbook" },
          { label: "Tenant", value: "tenant" },
        ],
        defaultValue: "session",
      },
      { key: "collection", label: "Collection", type: "text", placeholder: "default", defaultValue: "default" },
      { key: "record_key", label: "Record Key", type: "text", placeholder: "id", defaultValue: "id" },
      {
        key: "merge_strategy",
        label: "Merge Strategy",
        type: "select",
        options: [
          { label: "Replace", value: "replace" },
          { label: "Merge", value: "merge" },
        ],
        defaultValue: "replace",
      },
    ],
  },
  "error-handler": {
    title: "Error Handler Settings",
    description: "Normalize failures, emit status, and provide explicit fallback payloads for downstream recovery.",
    fields: [
      {
        key: "handler_mode",
        label: "Handler Mode",
        type: "select",
        options: [
          { label: "Fallback", value: "fallback" },
          { label: "Normalize", value: "normalize" },
          { label: "Escalate", value: "escalate" },
        ],
        defaultValue: "fallback",
      },
      { key: "fallback_value", label: "Fallback Value", type: "json", placeholder: '{"status":"degraded"}' },
      { key: "fallback_message", label: "Fallback Message", type: "textarea", placeholder: "Recovered from upstream failure." },
      { key: "retryable", label: "Retryable", type: "boolean", defaultValue: false },
      { key: "error_key", label: "Error Key", type: "text", placeholder: "message", defaultValue: "message" },
      { key: "emit_status", label: "Emit Status", type: "boolean", defaultValue: true },
    ],
  },
  wait: {
    title: "Wait Settings",
    description: "Configure delay, timeout, and resume windows for explicit orchestration pauses.",
    fields: [
      {
        key: "wait_mode",
        label: "Wait Mode",
        type: "select",
        options: [
          { label: "Delay", value: "delay" },
          { label: "Timeout Gate", value: "timeout_gate" },
          { label: "Resume Window", value: "resume_window" },
        ],
        defaultValue: "delay",
      },
      { key: "delay_ms", label: "Delay (ms)", type: "number", defaultValue: 1000 },
      { key: "timeout_ms", label: "Timeout (ms)", type: "number", defaultValue: 0 },
      { key: "simulate_wait", label: "Simulate Wait", type: "boolean", defaultValue: true },
    ],
  },
  output: {
    title: "Output Settings",
    description: "Control destination, formatting, and publication behavior.",
    fields: [
      {
        key: "destination",
        label: "Destination",
        type: "select",
        options: [
          { label: "Artifact Store", value: "artifact_store" },
          { label: "Webhook", value: "webhook" },
          { label: "Email", value: "email" },
          { label: "Queue", value: "queue" },
        ],
        defaultValue: "artifact_store",
      },
      { key: "format", label: "Output Format", type: "text", placeholder: "json | markdown | text" },
      { key: "schema_json", label: "Schema JSON", type: "json", placeholder: '{"type":"object"}' },
      { key: "version_policy", label: "Version Policy", type: "text", placeholder: "incremental" },
    ],
  },
};

export function normalizeNodeTypeForSchema(type: string): string {
  const raw = type.startsWith("frontier/") ? type.replace("frontier/", "") : type;
  if (raw.startsWith("agent/")) {
    return "agent";
  }
  return raw;
}

export function buildDefaultConfig(type: string): Record<string, unknown> {
  const schema = SCHEMAS[normalizeNodeTypeForSchema(type)];
  if (!schema) {
    return {};
  }

  const defaults: Record<string, unknown> = {};
  for (const field of schema.fields) {
    if (field.defaultValue !== undefined) {
      defaults[field.key] = field.defaultValue;
    }
  }

  if (normalizeNodeTypeForSchema(type) === "agent" && type.includes("/")) {
    const specificAgentId = type.split("/")[1];
    if (specificAgentId) {
      defaults.agent_id = specificAgentId;
    }
  }

  return defaults;
}

export function getNodeConfigSchema(type: string): NodeConfigSchema {
  const normalized = normalizeNodeTypeForSchema(type);
  return (
    SCHEMAS[normalized] ?? {
      title: "Node Settings",
      description: "No predefined schema for this node type yet. Use JSON for advanced configuration.",
      fields: [
        {
          key: "advanced_json",
          label: "Advanced JSON",
          type: "json",
          placeholder: '{"custom":"value"}',
        },
      ],
    }
  );
}
