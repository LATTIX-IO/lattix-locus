export type AppMode = "user" | "builder";

export type OperatorSession = {
  authenticated: boolean;
  actor: string;
  principal_id: string;
  principal_type: "user" | "agent" | "service" | "npe" | string;
  display_name: string;
  subject: string;
  email?: string;
  preferred_username?: string;
  auth_mode: string;
  provider?: string;
  roles: string[];
  capabilities: {
    can_admin: boolean;
    can_builder: boolean;
  };
  allowed_modes: AppMode[];
  default_mode: AppMode;
  oidc: {
    configured: boolean;
    issuer: string;
    audience: string;
    provider: string;
    validation_error?: string;
    browser_flow_configured?: boolean;
    browser_flow_error?: string;
  };
};

export type PlatformVersionStatus = {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  status?: "unknown" | "up_to_date" | "update_available";
  install_mode: "editable" | "wheel" | string;
  update_command: string;
  release_notes_url?: string;
  checked_at: string;
  source?: string;
  summary: string;
};

/** Runtime state of a security control, derived by the backend (LOCUS-313, P9). */
export type ControlState = "enforced" | "degraded" | "off" | "unverified";

export type ControlStatusItem = {
  id: string;
  label: string;
  state: ControlState;
  evidence: string;
};

export type ControlStatusReport = {
  controls: ControlStatusItem[];
  summary: Record<ControlState, number>;
  generated_at?: string;
};

export type PlatformHealthDetails = {
  status: string;
  timestamp: string;
  postgres: string;
  postgres_reason?: string;
  redis: string;
  long_term_memory: string;
  long_term_memory_reason?: string;
  memory_consolidation: string;
  memory_hybrid_retrieval: string;
  memory_world_graph: string;
  neo4j: string;
  control_status?: ControlStatusReport;
};

export type RunStatus =
  | "Running"
  | "Blocked"
  | "Needs Review"
  | "Done"
  | "Failed"
  | "Archived";

export type WorkflowRunKind = "workflow" | "chat" | "playbook" | "task";

export type RunKind = "individual" | "agent" | "workflow" | "playbook";

export type WorkflowRunSummary = {
  id: string;
  title: string;
  title_source?: "system" | "generated" | "user";
  status: RunStatus;
  updatedAt: string;
  progressLabel: string;
  kind?: RunKind | WorkflowRunKind;
};

export type WorkflowRunEvent = {
  id: string;
  type:
    | "user_message"
    | "agent_message"
    | "step_started"
    | "step_completed"
    | "guardrail_result"
    | "artifact_created"
    | "approval_required"
    | "approval_decision"
    | "tool_call"
    | "error";
  title: string;
  summary: string;
  content?: string;
  createdAt: string;
  metadata?: Record<string, unknown>;
};

export type ArtifactSummary = {
  id: string;
  name: string;
  status: "Draft" | "Needs Review" | "Approved" | "Blocked";
  version: number;
};

export type ArtifactDetail = ArtifactSummary & {
  run_id?: string | null;
  run_title?: string | null;
  createdAt: string;
  content: string;
};

export type GeneratedCodeArtifact = ArtifactSummary & {
  framework: "microsoft-agent-framework" | "langgraph";
  language: "python";
  path: string;
  summary: string;
  content: string;
  generated_at: string;
  entity_type: "agent" | "workflow";
  entity_id: string;
};

export type InboxItem = {
  id: string;
  runId: string;
  runName: string;
  artifactType: string;
  reason: string;
  queue: "Needs Review" | "Needs Approval" | "Clarifications Requested" | "Blocked by Guardrails";
};

export type WorkflowDefinition = {
  id: string;
  name: string;
  description: string;
  version: number;
  status: "draft" | "published" | "archived";
  published_revision_id?: string | null;
  published_at?: string | null;
  active_revision_id?: string | null;
  active_at?: string | null;
  security_config?: SecurityScopeConfig;
  graph_json?: {
    schema_version?: string;
    nodes?: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
    links?: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
  };
  generated_artifacts?: GeneratedCodeArtifact[];
};

export type SecurityClassification = "public" | "internal" | "confidential" | "restricted";

export type PlatformSignalEnforcement = "off" | "audit" | "block_high" | "raise_high";

export type SecurityScopeConfig = {
  classification?: SecurityClassification;
  guardrail_ruleset_id?: string | null;
  blocked_keywords?: string[];
  allowed_egress_hosts?: string[];
  allowed_retrieval_sources?: string[];
  allowed_mcp_server_urls?: string[];
  allowed_runtime_engines?: string[];
  allowed_memory_scopes?: string[];
  max_tool_calls_per_run?: number;
  max_retrieval_items?: number;
  max_collaboration_agents?: number;
  require_human_approval?: boolean;
  require_human_approval_for_high_risk_tools?: boolean;
  allow_runtime_override?: boolean;
  enable_platform_signals?: boolean;
  platform_signal_enforcement?: PlatformSignalEnforcement;
};

export type SecurityImmutableBaseline = {
  enforce_capability_filter: boolean;
  enforce_policy_gate: boolean;
  fail_closed_policy_decisions: boolean;
  enforce_signed_a2a_messages: boolean;
  enforce_a2a_replay_protection: boolean;
  require_readonly_rootfs_for_sandbox: boolean;
  require_non_root_sandbox_user: boolean;
  require_egress_mediation_when_network_enabled: boolean;
  allow_filter_chain_reordering: boolean;
  allow_custom_policy_code: boolean;
};

export type SecurityPolicyResponse = {
  immutable_baseline: SecurityImmutableBaseline;
  platform_defaults: Required<SecurityScopeConfig> & { classification: SecurityClassification; guardrail_ruleset_id: string | null };
  workflow_overrides: SecurityScopeConfig;
  agent_overrides: SecurityScopeConfig;
  effective: Required<SecurityScopeConfig> & { classification: SecurityClassification; guardrail_ruleset_id: string | null };
  /** Derived from control_status: only controls whose state is "enforced". */
  backend_enforced_controls?: string[];
  configurable_controls?: string[];
  control_status?: ControlStatusReport;
};

export type AgentDefinition = {
  id: string;
  name: string;
  version: number;
  status: "draft" | "published" | "archived";
  published_revision_id?: string | null;
  published_at?: string | null;
  active_revision_id?: string | null;
  active_at?: string | null;
  type: "form" | "graph";
  config_json?: {
    schema_version?: string;
    source_agent_id?: string;
    meta?: Record<string, unknown>;
    runtime?: {
      model_defaults?: Record<string, unknown>;
      engine_policy?: Record<string, unknown>;
      framework_mappings?: Record<string, Record<string, string>>;
      framework_profiles?: Record<string, Record<string, unknown>>;
      [key: string]: unknown;
    };
    reasoning?: Record<string, unknown>;
    knowledge?: Record<string, unknown>;
    integrations?: Record<string, unknown>;
    mcp?: Record<string, unknown>;
    a2a?: Record<string, unknown>;
    tools?: Record<string, unknown>;
    memory?: Record<string, unknown>;
    guardrails?: Record<string, unknown>;
    iam?: {
      principal_id?: string;
      principal_type?: "user" | "agent" | "service" | "npe";
      provider?: string;
      auth_mode?: string;
      display_name?: string;
      subject?: string;
      agent_id?: string;
      service_account_id?: string;
      client_id?: string;
      roles?: string[];
      groups?: string[];
      provisioning?: Record<string, unknown>;
      recommended_claims?: Record<string, unknown>;
      [key: string]: unknown;
    };
    security?: SecurityScopeConfig;
    graph_json?: {
      nodes?: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
      links?: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
    };
    [key: string]: unknown;
  };
};

export type GuardrailRuleSet = {
  id: string;
  name: string;
  version: number;
  status: "draft" | "published" | "archived";
  published_revision_id?: string | null;
  published_at?: string | null;
  active_revision_id?: string | null;
  active_at?: string | null;
  config_json?: Record<string, unknown>;
};

export type DefinitionRevisionSummary = {
  id: string;
  entity_type: "workflow_definition" | "agent_definition" | "guardrail_ruleset";
  entity_id: string;
  revision: number;
  action: string;
  version: number;
  status: string;
  created_at: string;
  actor: string;
  metadata?: Record<string, unknown>;
};

export type DefinitionRevisionHistory = {
  count: number;
  versions: DefinitionRevisionSummary[];
};

export type PlatformSettings = {
  org_name?: string;
  org_slug?: string;
  support_email?: string;
  website?: string;
  console_classification_banner_enabled?: boolean;
  console_classification_banner_text?: string;
  console_classification_banner_background_color?: string;
  console_classification_banner_text_color?: string;
  default_kickoff_workflow?: string;
  preferred_review_depth?: string;
  idle_timeout?: string;
  local_only_mode: boolean;
  mask_secrets_in_events: boolean;
  require_human_approval: boolean;
  require_human_approval_for_high_risk_tools?: boolean;
  emergency_read_only_mode?: boolean;
  block_new_runs?: boolean;
  block_graph_runs?: boolean;
  block_tool_calls?: boolean;
  block_retrieval_calls?: boolean;
  require_authenticated_requests?: boolean;
  require_a2a_runtime_headers?: boolean;
  a2a_require_signed_messages?: boolean;
  a2a_replay_protection?: boolean;
  default_guardrail_ruleset_id: string | null;
  global_blocked_keywords: string[];
  tenant_scoped_skills?: string[];
  collaboration_max_agents: number;
  max_tool_calls_per_run?: number;
  max_retrieval_items?: number;
  // Kept for stored-settings compatibility; the backend resolves every value to
  // the native engine and the single strategy (LOCUS-352).
  default_runtime_engine?: "native";
  default_runtime_strategy?: "single";
  default_hybrid_runtime_routing?: Partial<
    Record<"default" | "orchestration" | "retrieval" | "tooling" | "collaboration", "native">
  >;
  allowed_runtime_engines?: string[];
  allow_runtime_engine_override?: boolean;
  enforce_runtime_engine_allowlist?: boolean;
  enforce_egress_allowlist?: boolean;
  allowed_egress_hosts?: string[];
  enforce_local_network_only?: boolean;
  allow_local_network_hostnames?: string[] | boolean;
  allowed_retrieval_sources?: string[];
  retrieval_require_local_source_url?: boolean;
  allowed_mcp_server_urls?: string[];
  mcp_require_local_server?: boolean;
  high_risk_tool_patterns?: string[];
  enable_foss_guardrail_signals?: boolean;
  foss_guardrail_signal_enforcement?: PlatformSignalEnforcement;
  enforce_integration_policies?: boolean;
  require_signed_integrations?: boolean;
  require_sandbox_for_third_party?: boolean;
  allow_local_unsigned_integrations?: boolean;
  // AI inference providers (secret fields are write-only; *_configured flags
  // report whether a key is stored server-side).
  openai_api_key?: string;
  openai_api_key_configured?: boolean;
  openai_model?: string;
  openai_fallback_model?: string;
  nim_api_key?: string;
  nim_api_key_configured?: boolean;
  nim_base_url?: string;
  nim_default_model?: string;
  ollama_base_url?: string;
  ollama_default_model?: string;
  // Unified provider map (canonical). Secret api_key values are write-only;
  // the masked read adds api_key_configured per provider.
  ai_providers?: Record<
    string,
    { api_key?: string; api_key_configured?: boolean; base_url?: string; default_model?: string }
  >;
  // AI observability (LOCUS-375). Every exporter and content capture is off by
  // default; *_ref fields name a native secret, never hold its value.
  telemetry_capture_content?: boolean;
  telemetry_payload_retention_days?: number;
  telemetry_otlp_enabled?: boolean;
  telemetry_otlp_endpoint?: string;
  telemetry_otlp_auth_secret_ref?: string;
  telemetry_langsmith_enabled?: boolean;
  telemetry_langsmith_endpoint?: string;
  telemetry_langsmith_project?: string;
  telemetry_langsmith_api_key_ref?: string;
};

export type IntegrationDefinition = {
  id: string;
  name: string;
  type: "http" | "database" | "queue" | "vector" | "custom";
  status: "draft" | "configured" | "error" | "archived";
  base_url: string;
  auth_type: "none" | "api_key" | "bearer" | "oauth2" | "basic";
  secret_ref: string;
  secret_configured?: boolean;
  metadata_json?: Record<string, unknown>;
  capabilities?: string[];
  permission_scopes?: string[];
  data_access?: string[];
  egress_allowlist?: string[];
  publisher?: "first_party" | "third_party" | "custom";
  execution_mode?: "local" | "sandboxed";
  signature_verified?: boolean;
  approved_for_marketplace?: boolean;
  oauth_status?: IntegrationOAuthStatus;
};

export type IntegrationStarterTemplate = {
  id: string;
  wave: 1 | 2 | 3;
  name: string;
  summary: string;
  type: "http" | "database" | "queue" | "vector" | "custom";
  base_url: string;
  auth_type: "none" | "api_key" | "bearer" | "oauth2" | "basic";
  secret_ref: string;
  metadata_json?: Record<string, unknown>;
  capabilities?: string[];
  permission_scopes?: string[];
  data_access?: string[];
  egress_allowlist?: string[];
  publisher?: "first_party" | "third_party" | "custom";
  execution_mode?: "local" | "sandboxed";
  signature_verified?: boolean;
  approved_for_marketplace?: boolean;
};

export type MCPStarterTemplate = {
  id: string;
  wave: 1 | 2 | 3;
  name: string;
  summary: string;
  transport: "streamable_http" | "sse" | "custom";
  auth_type: "none" | "api_key" | "bearer" | "oauth2" | "basic" | "mcp_token";
  secret_ref: string;
  capabilities?: string[];
  permission_scopes?: string[];
  data_access?: string[];
  egress_allowlist?: string[];
  publisher?: "first_party" | "third_party" | "custom";
  execution_mode?: "local" | "sandboxed";
};

export type MCPConnectionDefinition = {
  id: string;
  starter_id: string;
  wave: 1 | 2 | 3;
  name: string;
  status: "draft" | "validated" | "validation_failed" | "approved" | "rejected" | "disabled";
  server_url: string;
  transport: "streamable_http" | "sse" | "custom";
  auth_type: "none" | "api_key" | "bearer" | "oauth2" | "basic" | "mcp_token";
  secret_ref: string;
  secret_configured?: boolean;
  metadata_json?: Record<string, unknown>;
  capabilities?: string[];
  permission_scopes?: string[];
  data_access?: string[];
  egress_allowlist?: string[];
  publisher?: "first_party" | "third_party" | "custom";
  execution_mode?: "local" | "sandboxed";
  approved_by?: string;
  approved_at?: string;
  last_validated_at?: string;
  last_validation_error?: string;
};

export type MCPConnectionValidationResponse = {
  ok: boolean;
  id: string;
  status: MCPConnectionDefinition["status"];
  validation: {
    ok: boolean;
    errors: string[];
    warnings: string[];
    checked_server_url: string;
  };
};

export type IntegrationOAuthStatus = {
  id: string;
  provider: string;
  grant_type: string;
  connected: boolean;
  pending: boolean;
  scopes: string[];
  authorize_url: string;
  token_url: string;
  client_id: string;
  redirect_uri: string;
  account_label: string;
  expires_at?: string | null;
  has_client_secret: boolean;
  has_refresh_token: boolean;
  has_access_token: boolean;
  last_error: string;
};

export type IntegrationOAuthConnectResponse = {
  ok: boolean;
  mode: "authorization_code" | "client_credentials";
  connect_url?: string;
  redirect_uri?: string;
  status: IntegrationOAuthStatus;
};

export type AgentTemplate = {
  id: string;
  name: string;
  description: string;
  category: "ops" | "security" | "sales" | "finance" | "general";
  status: "active" | "deprecated";
  config_json?: Record<string, unknown>;
};

export type PlaybookDefinition = {
  id: string;
  name: string;
  description: string;
  category: "go_to_market" | "security" | "support" | "operations" | "other";
  status: "draft" | "published" | "archived";
  metadata_json?: Record<string, unknown>;
  graph_json?: {
    nodes?: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
    links?: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
  };
};

export type TemplateCatalogItem = {
  id: string;
  source_id: string;
  template_type: "agent" | "workflow" | "playbook";
  name: string;
  description: string;
  category: string;
  status: "active" | "deprecated";
  version?: number | null;
};

export type ObservabilityRunTrace = {
  run_id: string;
  status: string;
  event_count: number;
  node_count: number;
  edge_count: number;
  duration_ms?: number;
  token_estimate?: number;
  cost_estimate_usd?: number;
  latency_by_stage_ms?: Record<string, number>;
};

export type AuditEvent = {
  id: string;
  action: string;
  actor: string;
  outcome: "allowed" | "blocked" | "error";
  created_at: string;
  metadata: Record<string, unknown>;
};

export type AtfAlignmentReport = {
  generated_at: string;
  framework: string;
  coverage_percent: number;
  maturity_estimate: "intern" | "junior" | "senior" | "principal";
  pillars: Record<
    "identity" | "behavior_monitoring" | "data_governance" | "segmentation" | "incident_response",
    {
      status: "partial" | "strong";
      controls: Record<string, unknown>;
      gaps: string[];
    }
  >;
  evidence: {
    audit_window_hours: number;
    audit_event_count_24h: number;
    audit_allowed_24h: number;
    audit_blocked_24h: number;
    audit_error_24h: number;
    total_audit_events: number;
    run_count_total: number;
  };
  control_status?: ControlStatusReport;
};

export type CollaborationParticipant = {
  user_id: string;
  principal_id?: string | null;
  principal_type?: "user" | "agent" | "service" | "npe";
  auth_subject?: string | null;
  display_name: string;
  role: "owner" | "editor" | "viewer";
  last_seen_at: string;
  metadata_json?: Record<string, unknown>;
};

export type CollaborationSession = {
  id: string;
  entity_type: "agent" | "workflow" | "playbook";
  entity_id: string;
  graph_json: {
    nodes?: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
    links?: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
  };
  version: number;
  updated_at: string;
  participants: CollaborationParticipant[];
};
