# Modular architecture: modules, ports and swappable implementations

Status: target architecture (D-28). It applies to all new work, and existing code converges on it through LOCUS-352 (thinning and the `main.py` split) and LOCUS-356.

Locus is built from **modules**. A module owns one job of an AI harness behind a typed **port** (its contract). The concrete code behind a port is an **implementation** (an adapter). An implementation can be replaced without touching the modules that use it, as long as it passes the port's contract tests. A third-party "plugin" is one kind of implementation; it is not a separate architecture.

## 1. Module map

| Module | Port(s) | Implementations (default first) | Swap class |
|---|---|---|---|
| **Agent runtime** | `AgentRuntime` | Bake-off winner (LOCUS-348: VerifiedLoop or Deep Agents) | Run boundary |
| **Model access** | `ModelProvider`, `ModelRouter` | NIM, Ollama, OpenAI-compatible, Anthropic | Live |
| **Tools and connectors** | `ToolProvider` | Built-in coding tools, MCP client (stdio/HTTP), integrations | Run boundary |
| **Computer use** | `BrowserDriver`, `DesktopDriver` | Agent browser (Playwright), user-profile bridge (D-25), Windows UIA / macOS AX or Cua drivers | Run boundary |
| **Memory** | `ColumnStore`, `ColumnKind` | **Cortical columns (native, D-10)** on SQLite | Release |
| **Knowledge and ingestion** | `Source`, `Parser`, `Embedder`, `Index`, `Retriever` | Upload and folder watch; Docling, MarkItDown; Ollama/NIM embeddings; sqlite-vec + FTS5 | Live (Parser, Embedder); run boundary (others) |
| **Skills** | `SkillStore` | Agent Skills format with capability manifest (D-18) | Live, through quarantine → trust |
| **Work intake and triggers** | `Tracker`, `Trigger` | Native tracker, Linear; cron, folder, MCP/A2A inbound | Run boundary |
| **Surfaces** | `SurfaceAdapter` over the application API | Desktop UI (AG-UI), CLI, MCP server, A2A, ACP | Release |
| **Evals and improvement** | `EvalSuite`, `Scorer` | Inspect AI suites, quality gates, loop runner | Release |
| **Persistence** | `StateStore`, `Checkpointer`, `AuditLog` | SQLite; hash-chained audit log | Release |
| **Observability** | `TelemetrySink` | Local OTel (GenAI conventions) to SQLite; optional Langfuse | Live |
| **Delivery and update** | `RepoDelivery`, `UpdateChannel` | git/gh delivery; Tauri updater channels (D-26) | Release |
| **Trust kernel** | `PolicyEngine`, `GrantIssuer`, `Sandbox`, `SecretStore`, `GuardrailClassifier` | OPA → Regorus (D-12); Biscuit; bwrap / seatbelt / AppContainer; keychain/DPAPI; Granite Guardian, Presidio | **Release only, principal-reviewed** |

## 2. Rules

1. **Contracts, not internals.** A module calls another module only through its port: Python `Protocol` interfaces plus Pydantic models in the module's `contract` package. Importing another module's internals is a CI failure, enforced with **import-linter** (BSD-2) layer and independence contracts.
2. **Versioned ports.** Each port carries a contract version. A breaking change needs a new major version and a deprecation window. Every implementation must pass the port's **contract test suite**, which is the admission test for an implementation.
3. **One composition root.** Implementations are selected in one place from a **profile**: a named configuration such as "Desktop operator", "Self-improvement dev" or "Headless". No module constructs another module's implementation.
4. **Each module owns its data.** There are no shared tables. Each module has its own schema and migrations, inside the shared SQLite file or its own file.
5. **The boundary is a security boundary.** Any cross-module call made on behalf of an agent goes through the gateway (P6). Modules receive capabilities (grants) and never ambient authority.
6. **Events for decoupling.** Modules publish typed events (run, ingestion, memory, update) to an in-process bus that is recorded in the audit log (P11). Consumers subscribe; producers don't know them.
7. **Self-describing.** Each module reports a manifest: name, port versions, active implementation, health and declared capabilities. The Posture page and the unified Settings page (LOCUS-353) are built from these manifests, so Settings is organized by module.

## 3. Swapping implementations

| Swap class | How it swaps | Who can swap |
|---|---|---|
| **Live** | A stateless implementation is replaced at once and the next call uses it; it falls back if the health check fails | The principal in Settings; the loop on a Dev build after the contract tests and scorecard pass |
| **Run boundary** | New runs use the new implementation; in-flight runs drain on the old one; the swap rolls back if the health check or contract tests fail | Same as live |
| **Release** | Ships in a new signed build through the update channels (D-26) | A merged PR (D-22 rules apply) |
| **Release only, principal-reviewed** | Trust-kernel implementations change only in a principal-reviewed release; never by runtime configuration, an agent or the loop | The principal |

The trust kernel is modular too, so its implementations can be replaced, for example the OPA sidecar with embedded Regorus. Only the swap path is restricted.

## 4. How the self-improvement loop uses it

The loop improves Locus one module at a time. It proposes a new implementation or a change behind a port, and the change must pass that port's contract tests, the quality gates (LOCUS-339) and the scorecard (LOCUS-351). It then ships through the Dev channel and is swapped in at the next run boundary. The trust kernel, the gates, the eval suites and the contract tests themselves are protected paths under D-22.

## 5. Third-party code

Third-party implementations run **out of process** as MCP servers (or A2A agents) inside the OS sandbox, go through quarantine → trust, and reach Locus only through the gateway. First-party implementations run in process. This takes the composability of plugin-style harnesses without their in-process trust model (see the 2026-10-03 review of DeepSeek Harness and OpenClaw).

## 6. Code layout (target)

```
locus/<module>/
  contract/        # Protocols, models, port version, contract tests
  impl/<name>/     # implementations
  manifest.py      # module manifest (health, capabilities, active implementation)
locus/composition/ # composition root and profiles
```

Existing packages (`locus_runtime`, `apps/backend/app`) move into this layout incrementally. The `main.py` split (LOCUS-352) follows these module lines.
