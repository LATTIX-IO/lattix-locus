# LOCUS-416 increment A — OpenCode external engine admission and event validation

## Objective

Create a versioned and testable OpenCode engine contract that the trusted
Locus supervisor can eventually hand to its fenced process executor. This is
a prerequisite for local Ollama/OpenCode and external BYO-CLI integration,
**not** a production execution adapter. It never starts an external process.

The durable task DAG, Gateway-issued child PodLease and trusted-engine registry
already exist on main. This increment compiles and validates the **agent
engine interface** independently of the **model provider interface**. The
model address is an operator-chosen `provider/model` such as
`ollama/qwen3:8b`, `openai/gpt-6` or an authorized NVIDIA/Mistral model.
A provider is never implicitly privileged because it is local.

### Trust boundary

1. The supervisor must independently authenticate the parent, accept the
   immutable task plan, attenuate child budget/capabilities, and issue its
   Gateway pod lease before considering an engine invocation.
2. The operator, not the prompt/model/skill/MCP server, selects an **absolute,
   pinned** OpenCode executable, accepted version, model, prepared private
   request/MCP files, and an exact allowlist of `locus_*` MCP tool names.
3. `compile_opencode_launch` emits a static `opencode run --format json`
   command and private, no-inherited-credentials environment. The prompt
   resides in a private attached file, **never** argv or environment.
   This planning API deliberately has no `subprocess` or network call.
4. V1 (`1.1.1+`) uses `permission`, direct `mcp.locus`, and deny-all
   except exact approved MCP names. V2 uses ordered `permissions`,
   `experimental.policies`, and `mcp.servers.locus` with Code Mode
   disabled; default tools/subagents/plugins/skills/filesystem/shell/web
   receive no grant. Unknown major/prerelease versions deny admission.
5. A trusted sandbox runner must verify the installed executable SHA-256,
   observed CLI version, isolated credential/config source namespace,
   actual *resolved* configuration and MCP identity before invocation.
   `validate_effective_opencode_config` refuses any additional config that
   could override tools, providers, project instructions or plug-ins.
   **The config compiler alone does not attest any OS boundary.**
6. A Locus-owned MCP server must bind every tool invocation to the admitted
   Gateway lease, durable idempotency key and fenced receipts; a vendor
   session ID is correlation metadata, not authentication. No standalone
   vendor tools, direct browser/computer use or arbitrary native CLI shell
   execution may bypass that broker.
7. `audit_opencode_jsonl` accepts bounded structured JSONL, checks one
   session ID, rejects native/unregistered tool activity and truncated or
   malformed streams, meters every step, and emits a content digest plus
   `complete_unverified`. Its result is **not proof** of accepted work;
   Locus's separate independent domain verifier must inspect a trusted
   artifact and promote it before dependent DAG nodes become runnable.

## Version-specific risks

- V1's `permission` object and V2's ordered `permissions` array are not
  interchangeable. V2 also nests MCP registrations under `mcp.servers`
  and defaults to Code Mode, which must be disabled when using the gated
  stdio MCP tool bridge.
- OpenCode merges remote/global/project/inline/managed configuration.
  The future runner must enforce isolated `HOME`, `XDG_*`, independent
  project scanning, no inherited credentials, and comparison against the
  resulting *effective* configuration. Environment flags alone are not a
  trustworthy guarantee that an installed build ignored a project plugin.
- For local Ollama the **future executor** must permit only its approved
  inference endpoint, not unrestricted egress. The existing PodSandboxBroker
  runs with `--network=none`; this PR intentionally does not disable that
  network control to make model inference work. A separately approved network
  broker/sidecar is required, with no direct secrets or host-network fallback.
- OpenCode's CLI output may change across builds. Each supported exact
  binary/version needs an executable handshake and realistic JSONL fixture.
  An exit code of zero, a final text event, or model-reported token usage
  alone is never verified work; missing a terminal `stop` must fail closed.
- This contract does not claim actual process kill-tree support, Windows
  containment, Docker Engine mount behavior, remote model egress attestation,
  long-running worker heartbeats, or browser/computer tool parity.

## Tests / definition of done for THIS bounded increment

`pytest -q tests/unit/test_opencode_contract.py`

Unit, regression and negative-security coverage includes:

- V1/V2 dialect negotiation, malformed/old/prerelease version refusal;
- operator-pinned CLI executable identity and provider-neutral models;
- no user prompt in argv, no auto-approval/share/attach/continue flags,
  isolated runtime/private request file paths, no ambient secret inheritance;
- catch-all deny and explicit only-Locus MCP allowlist, V2 hard-policy
  enforcement and no Code Mode;
- reject project/global/plugin/agent/extension config that tries to restore
  file, shell, web, skill or subagent authority;
- valid structured outputs, cross-session contamination, unknown tool,
  duplicate/missing tool calls, failed tool state, token budget over all steps,
  malformed/oversized/partial JSONL, trailing data, missing completion,
  and unverified result provenance;
- static guard that this module cannot execute a host process directly.

Full exact-head CI (backend unit, quality/type/policy/Helm, frontend, Windows,
integration, performance, DAST, and required-gates) is mandatory for merge.
Skipped CodeQL/AppSec/SBOM/SCA must be classified **NOT EXECUTED** and cannot
satisfy release security approval. Changes to CODEOWNERS/protected policy and
runtime executors are outside this increment.

## Follow-on: actual local smoke

A separate LOCUS-416 increment will implement an attested runner that:
- verifies executable image digest and `opencode --version`;
- stages private configuration/request/MCP files without writable host
  shadow paths; enforces config precedence using real `opencode debug config`;
- starts OpenCode inside an actually isolated sandbox, using network
  allowlisting to the Ollama inference proxy; never inherits user credentials;
- mediates every MCP/skill/browser/computer tool through the existing Locus
  Gateway, child lease and receipt ledger;
- parses real JSONL, enforces deadlines/token/tool budgets, kills process trees,
  and records independent artifacts/checkpoints for run recovery;
- validates a real local Ollama/OpenCode smoke and alternate-provider/CLI
  capability-negotiation smoke before production flags are enabled.

Rollback is simply omitting this unused compiler: no API migration,
principal privilege, action receipt, network route or durable state change
has been introduced.

Sources verified October 13, 2026:
- https://docs.opencode.ai/docs/config/
- https://docs.opencode.ai/docs/permissions/
- https://docs.opencode.ai/docs/cli/
- https://dev.opencode.ai/v2/docs/permissions/
- https://dev.opencode.ai/v2/docs/mcp-servers/
