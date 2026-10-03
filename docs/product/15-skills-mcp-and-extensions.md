# 15 · Skills, MCP and Extensions

Extensions are where most real capability comes from and where most supply-chain risk enters. Every extension has a **declared capability manifest**, runs **behind the gateway**, and moves through a **trust lifecycle**.

## 1. Skills

**Format:** the Agent Skills convention (a folder with `SKILL.md` frontmatter plus resources and scripts), the same format used by Claude Code, Codex and the repo's own `.claude/skills/`. Skills are portable between Locus and vendor agents.

**Locus manifest** (a `skill.locus.yaml` beside `SKILL.md`, or frontmatter keys):

```yaml
id: spiced-t-discovery
version: 1.3.0
areas: [bairesdev]           # where it may be trusted
tools:                       # gateway tools it may call
  - drive.search
  - drive.write: { paths: ["/Clients/**"] }
egress: [docs.google.com]
secrets: []                  # secret:// references, if any
risk_ceiling: R2             # highest action class it may request without approval
engines: { prefer: local, allow_hosted: true }
scripts: { sandbox: required }
```

A skill can never exceed the envelope of the run that invokes it. The manifest narrows; it never widens.

**Implemented today (LOCUS-340, `locus_runtime/skills.py`):** the capability manifest is read from frontmatter only, as `allowed-tools` plus `metadata.locus.capabilities` (or a top-level `locus.capabilities`) with the keys `tools`, `executables`, `egress`, `read_roots` and `write_roots` (roots are workspace-relative). A skill that declares nothing gets nothing: its scripts cannot run. Unknown capability keys are rejected. A script runs only for a trusted skill whose files still match the reviewed sha256. It runs in the sandboxed executor with network off, under a gateway session whose capabilities are the run envelope's intersected with the manifest. If the skill declares an egress host the envelope does not grant, the script is refused. The `skill.locus.yaml` sidecar and the `areas`, `secrets`, `risk_ceiling` and `engines` keys above are not implemented yet.

## 2. Skill lifecycle

| Stage | What happens |
|---|---|
| **Import** | From a folder, a git URL or a signed package. Origin recorded |
| **Scan** | Static checks (scripts, shell use, network calls, obfuscation); manifest vs content consistency; Laya injection noul over `SKILL.md` and resources; dependency provenance (P28) |
| **Evaluate** | Run the skill's test prompts (or generated ones) in a sandbox with recorded tool calls; compare against the manifest |
| **Trust** | Promote for one area or globally; signs the reviewed hash |
| **Update** | Any content change returns the skill to Scan; the previous trusted version keeps working until the new one is trusted |
| **Revoke** | Immediate; running invocations stop |

Skills authored by the agent (from a successful run, "save as skill") enter at Scan like any import.

## 3. MCP connections

The gateway is both an **MCP client** (to servers) and an **MCP server** (to engines).

```
Engine (native loop, Codex CLI, Claude Code) ──MCP──▶ Locus gateway ──MCP──▶ servers
                                                        │  grants · policy · taint · audit
                                                        └─ stdio servers run in Z3 sandboxes
```

| Concern | Rule |
|---|---|
| **Transports** | stdio servers launch inside a sandbox tier with their own egress allowlist; remote servers over Streamable HTTP with OAuth 2.1 |
| **Tool inventory** | On connect, every tool's name, description and schema are hashed and pinned. A change suspends that tool until reviewed (rug-pull protection) |
| **Risk classes** | Each tool gets a default R-class from annotations and review; the principal can raise it |
| **Descriptions as content** | Tool descriptions and outputs are untrusted; screened for injection before they enter context |
| **Auth** | OAuth tokens stored in the keychain, refreshed by the gateway (carry: OAuth connect/refresh exists today) |
| **Scoping** | A connection is enabled per area; runs only see tools enabled for their area and envelope |
| **Catalog** | A curated catalog of known servers with provenance and maintainer info; unknown servers are allowed but start untrusted |

## 4. Locus as an MCP server (H2)

Exposes a small tool surface to other clients (Claude Desktop, IDEs, other agents): `tasks.search`, `tasks.create`, `memory.search`, `locus.delegate` (create a task + envelope proposal). Calls from external clients are treated as a principal-equivalent only after the client is paired; delegation still requires local acceptance for R3-capable envelopes.

## 5. App integrations

First-party integrations are implemented as gateway tools (same rules as MCP tools) for the systems the principal relies on: Google Workspace, Microsoft Graph (mail, calendar, Teams, files), Slack, Linear, Asana, Jira, GitHub. Each declares actions with R-classes (for example `gmail.draft` R2, `gmail.send` R3).

## 6. Playbooks

A playbook is an envelope template plus optional plan skeleton, skills, engine preferences and assembly. Playbooks are edited in forms or YAML, viewed in diagram-js ([17](17-canvas-and-whiteboard.md)), versioned, and promoted like skills. They replace the workflow graph as the primary reusable unit; existing `frontier-graph/1.0` workflows import as playbooks with a plan skeleton.

## 7. Builder surfaces kept

The builder studio's versioning, publish, activate and rollback carry over to skills, playbooks, policies and assemblies. The React Flow workflow canvas is retired (D-08).
