# 17 · Canvas and Whiteboard

Two visual surfaces with different jobs, one index, and a deliberate path from the first to the second.

| | **Whiteboard** | **Model** |
|---|---|---|
| Job | Think: brainstorm, sketch, frame problems, freehand architecture | Structure: typed diagrams whose elements are data |
| Engine | Excalidraw (MIT) | diagram-js (bpmn.io, MIT) |
| Meaning lives in | Shapes, text, frames (loosely) | Typed objects and relationships in the database |
| Agent | Reads, searches, annotates; draws on request (H2) | Reads, queries, proposes changes as ghost edits |
| Size | Infinite, one per area ("forever") plus ad-hoc boards | Bounded per model |

## 1. When to use which

- **Whiteboard** when the shape of the problem isn't known yet: brainstorming, workshop capture, rough architecture, mind maps, planning a week.
- **Model** when the elements have types and the relationships matter to the system: architecture components and trust zones, task dependency maps, column assemblies, playbook structure, segmentation and policy views.
- **Forms or text** when the job is editing a definition (skills, policies, playbooks). The model view renders those definitions (P18).

## 2. Whiteboard

- **Forever board per area:** a single infinite canvas that accumulates frames over time, plus any number of named boards.
- **Frames** are first-class: titled, linkable from tasks and memory (`locus://boards/:id#frame`), and individually shareable to a space.
- **Elements:** shapes, arrows, text, sticky notes, images, embedded links to tasks and memory items (rendered as live chips).
- **Persistence:** the Excalidraw scene is stored as element JSON in the local database with per-frame versions (snapshots on idle and on explicit save).
- **Search and recall:** text, frame titles and connector topology are indexed into memory (§4).

## 3. Model (diagram-js)

- Every element binds to a **typed object** in the schema registry (component, zone, trust boundary, data store, task, column, policy) or is explicitly visual-only.
- **Model types** for H2: Architecture (components, flows, trust zones), Segmentation (zones and allowed paths, generated from and checked against policy), Task map (tasks and `blocks` relations), Assembly (columns and consensus policy), Playbook (plan skeleton).
- **Two-way binding:** editing a property in the inspector updates the diagram; editing the diagram updates the object (validated). Policy-generated views (segmentation) are read-only projections with "open the policy" actions.
- **Agent actionability:** canvas commands (read, query, propose changeset, place, layout, export) are gateway tools. Agent semantic edits render as ghost proposals accepted or rejected in place.
- **Why diagram-js:** a mature modelling toolkit (the base of bpmn-js) with a clean separation between model and rendering, a command stack with undo/redo, rules and modelling APIs that suit a data-centric backend, and a permissive license. React Flow is retired (D-08). If Kepler's JointJS+ vs diagram-js bake-off produces a reusable component layer, Locus adopts the same engine.

## 4. Whiteboard → memory and models

| Path | How |
|---|---|
| **Indexing** | On save, extract text, frame titles, sticky clusters and arrow topology into a board digest; embed; link to the area |
| **Recall** | "What did I sketch about Envoy authz?" retrieves frames with thumbnails and links |
| **Formalize to model** | "Turn this frame into an architecture model": the agent proposes typed elements and relations from the frame; accepted elements carry a `formalizes` link back to the frame |
| **Formalize to tasks** | "Make tasks from these stickies": proposes tasks (area, labels, project) in Triage, linked to the frame |
| **Memory proposals** | Decisions written on a board ("Decision: use Regorus") become decision proposals |

Board content is the principal's own (internal tier), but images and pasted content from elsewhere keep their original taint.

## 5. Engine selection and licensing

| Option | License | Assessment |
|---|---|---|
| **Excalidraw** | MIT | **Selected** for whiteboard. Embeddable React component, JSON scene format, collaboration-ready, permissive license |
| tldraw SDK | Commercial license key required in production; free hobby license only for non-commercial use with watermark | **Rejected** (P29): incompatible with AGPL distribution without per-deployment keys |
| Microsoft Whiteboard | Proprietary service | Not embeddable locally; import/export only if needed |
| diagram-js | MIT (bpmn.io) | **Selected** for models |
| React Flow | MIT | **Retired** in favour of diagram-js for data-centric modelling |

Both selected libraries must pass the provenance check (P28) and are pinned in the SBOM.

## 6. Collaboration (H3)

Boards and models shared in a space sync between peers with a CRDT document per board (scene elements keyed by id; last-writer-wins per element property). Presence (cursors, selection) is ephemeral and peer-to-peer. See [18](18-federation-and-collaboration.md).

## 7. Performance

Whiteboard: pan/zoom at 60 fps up to 10k elements on the forever board, with frame-level virtualization beyond that. Model: interactive editing up to 2k elements per model.
