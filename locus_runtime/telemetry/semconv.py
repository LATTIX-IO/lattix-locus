"""Pinned attribute names (OpenTelemetry GenAI semantic conventions + Locus).

The GenAI conventions are still "Development" status upstream, so every name
Locus emits is pinned here and nowhere else. A rename upstream is a one-file
change (and a ``PORT_VERSION`` bump of :mod:`.contract` when stored data
changes shape). Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
(GenAI spans, agent spans, events; checked 2026-10).

Locus-specific facts use the ``locus.`` namespace.
"""

from __future__ import annotations

#: The GenAI semconv release these names follow (informational, reported in posture).
GENAI_SEMCONV_VERSION = "1.37-development"

# -- GenAI: operation / provider / request / response -----------------------------
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
#: Current name of the provider attribute.
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
#: Deprecated alias still read by several backends (Langfuse, LangSmith); emitted too.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_OUTPUT_TYPE = "gen_ai.output.type"

# -- GenAI: agents and tools ---------------------------------------------------------
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_ID = "gen_ai.agent.id"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"

# -- GenAI: opt-in content (only ever set when content capture is on, redacted) ------
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GEN_AI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"

#: Attributes that carry message / tool content. Stored as payload (pruned after
#: the payload retention), never set unless content capture is on.
CONTENT_ATTRIBUTES: frozenset[str] = frozenset(
    {
        GEN_AI_INPUT_MESSAGES,
        GEN_AI_OUTPUT_MESSAGES,
        GEN_AI_SYSTEM_INSTRUCTIONS,
        GEN_AI_TOOL_CALL_ARGUMENTS,
        GEN_AI_TOOL_CALL_RESULT,
    }
)

# -- GenAI: evaluation results (scores) ---------------------------------------------
GEN_AI_EVALUATION_EVENT = "gen_ai.evaluation.result"
GEN_AI_EVALUATION_NAME = "gen_ai.evaluation.name"
GEN_AI_EVALUATION_SCORE_VALUE = "gen_ai.evaluation.score.value"
GEN_AI_EVALUATION_SCORE_LABEL = "gen_ai.evaluation.score.label"
GEN_AI_EVALUATION_EXPLANATION = "gen_ai.evaluation.explanation"

# -- General -------------------------------------------------------------------------
ERROR_TYPE = "error.type"
SERVER_ADDRESS = "server.address"

# -- Operation names ----------------------------------------------------------------
OP_CHAT = "chat"
OP_INVOKE_AGENT = "invoke_agent"
OP_EXECUTE_TOOL = "execute_tool"
#: Locus operations (not GenAI): gateway decision, sandbox exec, loop tick, gate.
OP_GATEWAY = "locus.gateway.authorize"
OP_SANDBOX_EXEC = "locus.sandbox.exec"
OP_LOOP_TICK = "locus.loop.tick"
OP_GATE = "locus.gate"
OP_EVALUATION = "locus.evaluation"

# -- Locus attributes ---------------------------------------------------------------
LOCUS_RUN_ID = "locus.run.id"
LOCUS_RUNTIME = "locus.runtime"
LOCUS_END_STATE = "locus.run.end_state"
LOCUS_VERIFIED = "locus.run.verified"
LOCUS_STEPS = "locus.run.steps"
LOCUS_ACTIONS = "locus.run.actions"
LOCUS_COST_USD = "locus.usage.cost_usd"
LOCUS_COST_KNOWN = "locus.usage.cost_known"
LOCUS_USAGE_REPORTED = "locus.usage.reported"
LOCUS_STREAM = "locus.model.stream"
LOCUS_FALLBACK_HOP = "locus.model.fallback_hop"
LOCUS_FALLBACK_FROM = "locus.model.fallback_from"
LOCUS_FALLBACK_EVENT = "locus.model.fallback"
LOCUS_FALLBACK_REASON = "locus.model.fallback_reason_code"
LOCUS_FALLBACK_TO = "locus.model.fallback_to"
LOCUS_GATEWAY_AUDIT_ID = "locus.gateway.audit_id"
LOCUS_GATEWAY_ACTION = "locus.gateway.action_kind"
LOCUS_GATEWAY_TOOL = "locus.gateway.tool"
LOCUS_GATEWAY_OUTCOME = "locus.gateway.outcome"
LOCUS_GATEWAY_RISK = "locus.gateway.risk"
LOCUS_GATEWAY_REASONS = "locus.gateway.reasons"
LOCUS_GATEWAY_POLICY_VERSION = "locus.gateway.policy_version"
LOCUS_GATEWAY_DECISIONS = "locus.gateway.decisions"
LOCUS_TOOL_STATUS = "locus.tool.status"
LOCUS_TOOL_REFUSED_EVENT = "locus.tool.refused"
LOCUS_SANDBOX_BACKEND = "locus.sandbox.backend"
LOCUS_SANDBOX_TIER = "locus.sandbox.tier"
LOCUS_SANDBOX_NETWORK = "locus.sandbox.network"
LOCUS_SANDBOX_EXECUTABLE = "locus.sandbox.executable"
LOCUS_SANDBOX_EXIT_CODE = "locus.sandbox.exit_code"
LOCUS_SANDBOX_TIMED_OUT = "locus.sandbox.timed_out"
LOCUS_SANDBOX_DURATION_MS = "locus.sandbox.duration_ms"
LOCUS_LOOP_STATUS = "locus.loop.status"
LOCUS_LOOP_ISSUE = "locus.loop.issue"
LOCUS_GATE_KIND = "locus.gate.kind"
LOCUS_GATE_STATUS = "locus.gate.status"
LOCUS_SCORE_SOURCE = "locus.score.source"
LOCUS_CONTENT_TRUNCATED = "locus.content.truncated"

#: Gateway outcomes, worst last (a tool span reports the worst decision it saw).
GATEWAY_OUTCOME_ORDER: tuple[str, ...] = ("allow", "ask", "deny")
