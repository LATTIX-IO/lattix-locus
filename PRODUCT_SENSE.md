# Lattix xFrontier Product Sense

xFrontier exists so an operator can build, run, and audit multi-agent work on infrastructure they control. The differentiator is not the agents — it is that every step is governed, isolated, and inspectable, and that the whole thing runs locally without phoning home.

## Primary journeys

| Journey | Surface | What must be true |
| --- | --- | --- |
| Install and run locally | `install/bootstrap.{sh,ps1}` → `lattix up` | One command to a working secure stack; update and remove are non-destructive to user work |
| Build a workflow | `/builder/workflows/[id]` ReactFlow canvas | Node config is schema-driven; validation happens before a run, not during it |
| Define an agent | `/builder/agents/[id]` | Security scope and guardrail binding are part of the definition, not an afterthought |
| Run and watch | `/workflows/start`, `/runs/[id]` | Progress, per-node output, and cognition state are visible while running |
| Approve a gated step | `frontier/human-review`, `/inbox`, `/approvals` | Nothing state-changing proceeds past a gate without an explicit decision |
| Audit afterwards | `/audit/events`, `/observability/runs/{id}/trace`, run audit tabs | A reviewer can reconstruct what ran, under what policy, with what evidence |
| Publish and roll back | publish / activate / rollback on every definition type | Version history is real; rollback is a first-class action, not a restore-from-backup |
| Operate the platform | `lattix health`, `/healthz/details`, `/platform/security-policy` | Posture is inspectable at runtime, not just at deploy time |

## Product promises

- **Local-first is real.** The platform runs on operator-controlled infrastructure. No mandatory external control plane.
- **Governed by default.** Guardrails, OPA policy, capability tokens, and egress allowlists are part of the product, not an enterprise add-on.
- **Nothing executes unsandboxed.** Tool execution goes through a declared isolation strategy with declared capabilities.
- **Every run is auditable.** Hash-chained, signed events; run traces; audit endpoints.
- **Human gates hold.** Approval nodes block, and the block is enforced server-side.
- **Versioning is honest.** Publish, activate, and roll back are explicit operations with retained revisions.
- **Additive evolution.** New capability does not break graphs an operator already built.

## Anti-goals

- Do not turn the builder into an unaudited privileged shell.
- Do not let convenience defaults weaken posture. `local-lightweight` is for a laptop; anything else pins `local-secure` or `hosted`.
- Do not hide security state behind the UI. If a run was allowed by a policy exception, say so.
- Do not require a proprietary model provider. Engine selection is pluggable and its readiness is reported.
- Do not ship private or customer agent definitions in this repository. Demo assets live in `examples/agents/`; private assets come from `FRONTIER_AGENT_ASSETS_ROOT`.
- Do not log prompts, tool payloads, memory contents, tokens, or session IDs without redaction.
- Do not claim a capability the code does not have. If a layer is codegen rather than execution, describe it that way.

## Honest current limits

State these rather than papering over them:

- The **cognitive slice is 4 columns** (goal, evidence, assembly, commitment). The system is still agent-centric and sequential.
- **Microsoft Agent Framework is code generation, not execution.** `generated_artifacts.py` emits `agent_framework` source; nothing imports or runs it.
- **Control-plane definitions can be lost on restart** if Postgres is unconfigured or unreachable, silently.
- **`k8s-gvisor` and `k8s-kata` are enum values with no implementation.**
- **Quality gates are currently red** — `pytest` does not collect, `ruff` reports 24 errors including a latent `NameError`, and the gated `mypy` scope reports 38. See `QUALITY_SCORE.md`.
