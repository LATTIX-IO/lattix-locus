# 14 · Memory and Knowledge

Memory is what makes the operator yours. It must be **useful** (it changes what the agent does), **governed** (you can see and fix it) and **bounded** (it never leaks across areas, peers or engines it shouldn't).

## 1. Memory types

| Type | What it holds | Example | Lifetime |
|---|---|---|---|
| **Working** | Run-scoped scratch: plan state, intermediate results, column beliefs | "The SOW draft is at /Bain/POC/v2.docx" | Expires with the run (summarized into an episode) |
| **Episodic** | What happened: runs, conversations, meetings, decisions in context | "On 2 Oct we reviewed Locus state; main is regressed" | Durable, decays in retrieval weight |
| **Semantic** | Facts about the principal's world | "The client's data platform lead owns the POC budget" | Durable until superseded |
| **Decision** | Choices made, with rationale and date | "Use Regorus for embedded policy (D-12)" | Durable; supersession is explicit |
| **Preference** | How the principal wants things done | "Drafts in direct, minimal-fluff style" | Durable |
| **Procedural** | How to do things; becomes or links to skills and playbooks | "To file a SPICED-T doc, use the pod folder in the Clients drive" | Durable, versioned |
| **Project state** | Status of ongoing efforts | "Lattix ARQ: hosting decision pending" | Durable, review-dated |

The memory record schema should be compatible with the repo's existing AGENTS.md memory protocol (`mem_type`, `scope`, `project`, `source`, `confidence`, `status`, `evidence`, `operational_impact`, `update_rule`) so memories can move between Locus and coding-agent repos.

## 2. Scopes

`run` → `area` → `domain` (all areas) and separately `space` (shared with peers). Retrieval in a run is limited to the run, its area, and domain-level items marked cross-area (for example preferences). Space memory is visible only inside that space. Engines see only what retrieval returns for the run, filtered by the area's data ceiling.

## 3. Lifecycle and governance

```
capture (conversation, run, board, source, sync) → proposal
   → Review (accept · edit · merge · reject)            [batch, keyboard]
   → Active → (review date) → reconfirm | supersede | forget
```

- **Proposals** come from the agent, columns and source processing; each states its evidence and confidence.
- **Auto-accept** is allowed only for low-risk types the principal enables (for example episodic summaries of their own runs).
- **Forget** deletes content and embeddings, and anything derived solely from it, leaving only an audit stub.
- **Contradictions:** a new item that conflicts with an active one is proposed as a supersession with both shown.
- **Sensitive content:** a Laya sensitivity score plus rules keep certain categories (credentials, financial account numbers, health details) out of durable memory unless explicitly added by the principal.

## 4. Cognitive columns and memory

Columns are the "thousand brains" layer on top of the store.

- Each **column** maintains its own model of the current subject (belief state + evidence references + confidence) and retrieves its own slice of memory: the Evidence column pulls sources and decisions; the Risk column pulls policies, past incidents and denials; the Domain column pulls area knowledge.
- **Independent retrieval** is deliberate: columns don't share one context window, so a poisoned or misleading memory item is less likely to sway every column.
- An **assembly** fuses column outputs into a **commitment**; dissent is recorded and can be inspected later ("why did it ask me?" → the Risk column dissented, citing a prior denial).
- Columns **propose** memory in their domain. Over time, column-specific memories (risk patterns, verification heuristics, domain facts) become the operator's learned judgment, which is reviewable like any other memory.
- Column calibration (how often each column's votes matched outcomes and user decisions) shows on Memory → Columns.

## 5. Storage and retrieval

- **Store:** on the desktop, an embedded SQLite file (FTS5 full-text index + sqlite-vec vectors) behind the memory port, on by default with no setup and a default **Personal** collection (LOCUS-387); PostgreSQL + pgvector on the full stack. Memory items are rows with JSON attributes; embeddings per chunk; full-text index.
- **Retrieval:** hybrid lexical + vector shortlist → Laya rerank (score) → top-k with citations. Graph hops along relationships (task ↔ memory ↔ board ↔ source) for "what's related" queries.
- **Embeddings:** a local embedding model by default (provenance-filtered); hosted embeddings only where area policy allows.
- **World graph:** the earlier world-graph and consolidation features (behind ~40 flags, mostly off) are consolidated into this model: one store, relationship tables, a deterministic consolidation job. Flags that never ship are removed.

## 6. Knowledge sources

| Source | Behavior |
|---|---|
| Granted folders | Indexed collections (carry: knowledge/RAG from `ae4d703`); watchers keep them fresh |
| Boards and models | Text, frames, connectors and typed elements indexed ([17](17-canvas-and-whiteboard.md) §4) |
| Tracker | Tasks, comments and project updates are first-class memory sources |
| Connected apps | Mail, calendar, docs and M365 Copilot retrieval results, as cited sources (not auto-promoted) |
| External memory | H2 import: the AGENTS.md memory inbox and an Obsidian vault, as proposals |

## 7. Memory UX

- **Items:** a list with type, area, status and confidence filters; inline edit; "why does it believe this?" shows evidence.
- **Review:** grouped proposals (by area and type) with keyboard accept/edit/reject and merge suggestions.
- **In context:** answers cite memory items; any citation opens the item in the inspector for correction.
- **Explain recall:** each run's Evidence tab lists the memory items that each column retrieved.
