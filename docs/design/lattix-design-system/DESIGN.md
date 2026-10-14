---
name: "Lattix"
category: Brands
surface: web
colors:
  canvas: "#f4f4f1"
  foreground: "#1c1d1b"
  interaction: "#1f5fd6"
  surface: "#ffffff"
  muted: "#5e6059"
  border: "#dcdcd6"
---

# Lattix

> Category: Brands

> Surface: web

Lattix develops advanced Zero Trust, data-centric security infrastructure for enterprises, government, defense, and mission-critical environments. Its platform provides cryptographic data protection, fine-grained policy enforcement, verifiable provenance, secure collaboration, distributed systems management, and AI data governance.

Lattix's brand identity is minimalist, precise, and technically sophisticated. It draws on modern defense command-and-control systems and operational intelligence platforms. The intended feel is a precision-engineered mission system: authoritative, controlled, calm, and highly functional.

The visual foundation is neutral. Black, charcoal, graphite, white, and warm grey carry the hierarchy. Colour is never decoration. It appears for exactly three reasons: to show where the user can act (one interaction colour), to show abnormal operational state (a priority-ordered alarm palette), and to mark things that are not real (simulated, proposal, shadow mode). Do not use yellow as a recurring brand accent.

IBM Plex Sans carries interface and reading text. IBM Plex Mono carries identifiers, numbers, timestamps, telemetry, and code. Layouts favour clean geometry, thin borders, deliberate alignment, layered surfaces, and minimal elevation.

The brand voice is declarative, concise, technically grounded, and confident without exaggeration. Write like a senior security architect communicating with experienced operators, engineers, analysts, and executives. Use accurate terminology, direct action labels, and precise status descriptions. Avoid marketing hype, unnecessary jargon, playful language, and decorative copy.

Core design philosophy: Maximum operational capability. Minimum visual noise. Minimum cognitive overhead. Unmistakable state.

## Design System Identity

| Property | Value |
| --- | --- |
| Brand | Lattix |
| Legal entity | Lattix Technologies Corp. |
| Product | Lattix Mesh Dashboard |
| Design system | Lattix Operational Interface System — blended model (2026-10-11) |
| Typefaces | IBM Plex Sans (interface, 400–600) · IBM Plex Mono (data, 400–600) |
| Primary action | Interaction blue — #1F5FD6 on light, #2F62D4 on dark, white label. One per decision point. |
| Status | ISA-101 priorities: P1 critical, P2 warning, P3 advisory. Normal is grey. |
| Not real | Violet — simulated, proposal, draft, shadow mode |
| Radius | 4px tags/status · 6px controls · 8px panels, dialogs, drawers |
| Controls | 32px compact · 36px comfortable · 44px touch |
| Spacing | 4px baseline grid |
| Label case | Sentence case everywhere |

# Lattix Operational Interface System
## UI/UX design and component generation rules (blended model)

The system must support conventional administration alongside advanced analytical workspaces: policy decision analysis, knowledge graphs, data lineage, AI execution traces, distributed service topology, cryptographic provenance, and real-time operational monitoring. It combines the information density of command systems with the clarity and predictability of well-designed enterprise software.

## 1. Three Layers

Each layer owns one job. Where they disagree, the operations layer wins on status, the component layer wins on interaction, and the brand layer wins on everything else.

| Layer | Source | Owns |
| --- | --- | --- |
| Brand | Lattix | Neutral palette, IBM Plex Sans + Mono, logo, voice, absence of decoration |
| Components | Kumo-derived component practice | Role-named tokens, control sizes, states, radii, focus, motion, label casing, chart palettes |
| Operations | ISA-101 high-performance HMI | When colour may appear, alarm priorities, shape redundancy, operating-mode framing |

The previous monochrome-only rules (neutral primary, uppercase labels, 2px everywhere, Mono for all text) are retired. They made actions, selection, and status indistinguishable and slowed reading. The superseded specification is archived at `_archive/pre-blended-2026-10-11/`.

## 2. Visual Foundations

- Neutral foundation in both themes, built from shared semantic tokens. Dark and light are equally supported.
- Surfaces layer by role: canvas (`bg`) → panel (`surface`) → raised (`elevated`); tags, tracks, and nested cards use `recess`. A bordered card is never nested inside a bordered card.
- Light: canvas #F4F4F1, panels #FFFFFF, text #1C1D1B, borders #DCDCD6. Dark: canvas #121312, panels #1B1C1B, raised #232423, text #ECECE6, borders #2F312E.
- Thin 1px hairlines between flat surfaces; `border-strong` (light #84867E, dark #6E7069) for control boundaries so they meet 3:1.
- Radii: 4px for tags, chips, and status cells; 6px for controls; 8px for panels, cards, dialogs, drawers. Pills (`radius-pill`) only for toggles and circular icon actions. Nested shapes use concentric radii.
- Flat surfaces. Shadows only on overlays (drawers, popovers, dialogs).
- 4px spacing rhythm: 4–8px for related text, 8–12px for control clusters, 16px card interiors, 24–32px between independent task groups.

Avoid gradients (except the simulation mode band hatch), glassmorphism, glow, neon, 3D, ornamental HUD effects, and decorative animation.

## 3. Colour Roles

Choose colour by role, never by hue. Raw hex values live only in the token file.

| Role | Tokens | Means | Never used for |
| --- | --- | --- | --- |
| Interaction | `primary-bg`, `link`, `focus`, `selected` | You can act here; this is selected; this has focus | Status |
| P1 critical | `critical`, `critical-fg`, `critical-bg` | Blocked, failed, denied, restricted, unqualified | Decoration, headings |
| P2 warning | `warning`, `warning-fg`, `warning-bg` | Degraded, conflict, near threshold | Normal or advisory states |
| P3 advisory | `advisory`, `advisory-fg` | At risk, stale, idle — watch | Fills |
| Normal | `normal` | Running, healthy, enforced, qualified | Text blocks, fills, borders — a small dot only |
| Not real | `sim`, `sim-fg`, `sim-bg` (alias `info`) | Simulated, proposal, draft, shadow mode, demo | Actions |
| Destructive | `destructive-bg`, `destructive-fg` | Irreversible operations | Status |

Rules:
1. Normal is grey. A screen where everything is healthy shows almost no colour.
2. Colour scales with priority: P1 gets a tint, a leading bar, and bold text; P2 a tint; P3 an outlined marker only.
3. Blue is never a status. Informational and not-real states use violet so blue keeps one meaning.
4. Colour is never the only signal.

## 4. Status Grammar

Every state is shown with a shape and a word. Status is always paired with text.

| Priority | States | Shape | Treatment |
| --- | --- | --- | --- |
| P1 critical | Blocked, failed, denied, restricted | ▲ triangle | Tint + 3px leading bar + 600 weight |
| P2 warning | Degraded, conflict, near threshold | ◆ filled diamond | Tint |
| P3 advisory | At risk, stale, idle | ◇ outlined diamond | Neutral surface, outlined marker |
| Normal | Healthy, enforced, running | ● dot | Neutral text, small dot |
| Not real | Simulated, proposal, shadow mode, draft | □ outlined square | Violet tint |
| Unknown | Pending, no data | ◌ dashed circle | Muted text |

Alert summaries count by priority (P1 ▲ n, P2 ◆ n) with an accessible label, rather than a single undifferentiated badge.

## 5. Operating Modes

The operating mode is a frame, not a tag. It appears on every page through a band on the top edge of the application plus a labelled pill in the top bar that states the mode and what it means.

| Mode | Label | Meaning | Band |
| --- | --- | --- | --- |
| Observation | Observation | Read-only telemetry | Neutral (`border-strong`) |
| Simulation | Simulation | No enforcement or equipment connection | Hatched violet (`sim`) |
| Governed execution | Governed execution | Commands reach live systems | Solid red (`governed`), 4px |

Entering governed execution requires an explicit confirmation. No page may opt out of the frame.

## 6. Typography

- IBM Plex Sans for all interface and reading text; IBM Plex Mono for IDs, numbers, timestamps, telemetry, and code (inline Mono is optically about 0.9em).
- Compact density: 13px body, 12px labels. Comfortable density: 14px body, 12px labels. Headings 16 / 20 / 24px.
- Sentence case everywhere — headings, labels, buttons, table headers, tags, mode names. No uppercase labels and no added letter-spacing.
- Weight 600 for headings, 500 for emphasis and control labels, 400 for copy. Do not use 700 as the default emphasis.

## 7. Components

Required component families: navigation (collapsible sidebar, breadcrumbs, tabs, command search), actions, inputs, data display, contextual interaction (inspectors, drawers, popovers, dialogs), feedback, analytical views, and the operational shell (mode frame, priority counts, safety status).

| Component | Rule |
| --- | --- |
| Buttons | Secondary (surface + border) is the default. One primary (interaction blue) per decision point. Tertiary for low emphasis. Destructive only for irreversible acts. Disabled keeps its shape and uses muted text, not opacity alone. |
| Sizes | 32px compact, 36px comfortable, 44px on coarse pointers. Small 26px. |
| Tags | Borderless recessed pill, 4px radius. Never look like buttons. |
| Status cells | Flat, no border on normal cells. P1: tint + leading bar. P2: tint. P3: outline. Selection: 2px interaction inset. |
| Segmented controls | Recessed track, raised pressed segment. |
| Tabs and navigation | Current item marked with an interaction-colour underline or inset bar; hover is a neutral tint. |
| Tables | Hairline rows. Selected row: `selected` tint + interaction leading bar. Names in tables read as text and take the link colour on hover. |
| Links | `link` colour with a soft underline that strengthens on hover. |
| Focus | 2px `focus` ring, 2px offset, on every interactive element and background. |
| Inspector / drawers | Right side, 480–520px, shadowed; footer holds exactly one primary. |

## 8. Operational UX Philosophy

Design for experienced operators performing complex work under time pressure.

**Immediate situational awareness:** current state, abnormal conditions, operating mode, and available actions are recognisable at a glance.

**Progressive disclosure:** essential operational information first; technical metadata, evidence, and analysis through contextual inspection.

**Minimal interaction cost:** common tasks take one or two meaningful interactions when security permits.

**Context preservation:** retain filters, selections, time ranges, expanded views, and scope while investigating.

**Predictable interaction:** every component behaves the same everywhere. Prefer explicit, discoverable controls over hidden gestures.

**Calm hierarchy:** normal stays quiet so abnormal stands out. Reserve strong emphasis for genuinely important conditions.

Do not confuse information density with cognitive overload.

## 9. Tables, Filters, and Operational Data

Tables are foundational. Support sorting, filtering, search, column configuration, selection, bulk operations, and contextual inspection. Use compact rows, hairline dividers, Mono for identifiers and numbers, and restrained hover states. Provide access to underlying records and evidence directly from the data. Status cells follow the status grammar.

## 10. Application Layout

- Full-width top bar holding the logo, page tabs, safety status, priority counts, operating-mode pill, and user controls.
- Collapsible left navigation below it: icons with tooltips (about 52px) or icons with labels (about 200px), remembered per user.
- Context row for scope, page actions, and the single primary action.
- Main workspace occupying most of the screen; optional right-side inspector.
- Below 900px the navigation becomes a bottom bar and page tabs move to their own row.

Avoid generic dashboards of oversized KPI cards. A dashboard exists to support decisions and actions.

## 11. Information Density and Hierarchy

Level 1 — operational overview: state, abnormal conditions, primary controls.
Level 2 — contextual investigation in a drawer or inspector without leaving the workspace.
Level 3 — deep analysis: evidence, decision reasoning, provenance, history, configuration.

Offer compact (monitoring) and comfortable (workflows) density, switchable per page type.

## 12. Advanced Analytical Workspaces

Graphs, policy analysis, AI security traces, service topology, audit and provenance, and operational timelines use the same tokens, typography, geometry, and interaction behaviour as administrative screens. Selection uses the interaction colour; health uses the status grammar; failures use P1. Graphs prioritise legibility over spectacle and provide a table alternative.

Charts choose palettes by task: status palette for health or severity, a categorical palette for nominal series (never reusing status colours), and a single-hue sequential ramp for magnitude.

## 13. Interaction and Motion

- Place actions near the entities they affect; make the primary action immediately discoverable.
- Contextual menus for secondary operations; inline editing when safe.
- Keyboard shortcuts for frequent expert operations.
- Confirmation dialogs for destructive or high-impact actions, including entering governed execution.
- Clear feedback for asynchronous operations; simulated and proposal results are labelled as such.
- Never sacrifice authorization, security validation, or auditability to reduce clicks.
- Hover colour changes are instant. Small state changes 150ms; drawers and navigation 250ms on cubic-bezier(0.77, 0, 0.175, 1). Respect prefers-reduced-motion. No pulsing, continuous, or cinematic animation.

## 14. Accessibility and Readability

Target WCAG 2.2 AA. Every token pair in the canonical file is contrast-checked (4.5:1 text, 3:1 boundaries, markers, and focus). Visible focus on every background. Accessible names for icon-only controls. Never communicate state through colour alone. Graphs and complex visualisations have structured alternative views. Touch targets are 44px on coarse pointers.

## 15. Design Acceptance Criteria

A generated design is acceptable only when:

- The screen's primary purpose and the operating mode are immediately understandable.
- Abnormal states stand out because normal states are quiet.
- Each decision point has exactly one primary action, in the interaction colour.
- Selection and focus are visually distinct from status.
- Simulated, proposal, and shadow-mode content is violet and never looks actionable.
- Labels are sentence case; data uses Mono, prose uses Sans.
- Complex information is organised rather than concealed or dumped.
- Dark and light modes keep equivalent function and hierarchy.
- The result feels like a production-grade defense and enterprise security platform, not a generic SaaS dashboard or fictional HUD.

## 16. Screen Generation Rules

1. Identify the primary user task and decision.
2. Determine the minimum information needed to complete it.
3. Place the single primary action close to its context.
4. Present operational state before supplementary information, using the status grammar.
5. Make deeper detail available through contextual inspection.
6. Reuse existing tokens, components, and layout conventions.
7. Keep normal states neutral; apply colour only for interaction, abnormal state, or not-real content.
8. Show the operating mode frame.
9. Support both themes and both densities.
10. Evaluate clicks, visual noise, and cognitive load before finalising.

**Overall objective:** a precision-engineered operational workbench where sophisticated capabilities remain accessible, workflows are efficient, state is unmistakable, and the interface stays calm.

## Theme Token Reference

Canonical semantic tokens live in canonical/lattix-tokens.json and compile to system/lattix-theme.css. Generated --brand-* variables are overridden by that layer after every regeneration.

### Light Mode

| Role | Name | Hex | Usage |
| --- | --- | --- | --- |
| bg | Canvas | `#f4f4f1` | page canvas |
| surface | Surface | `#ffffff` | panels and cards |
| recess | Recess | `#ececE8` | tags, tracks, nested cards |
| fg | Foreground | `#1c1d1b` | body text and headings |
| muted | Muted | `#5e6059` | secondary text and metadata |
| border | Border | `#dcdcd6` | hairlines |
| border-strong | Control border | `#84867e` | control boundaries (3:1) |
| primary-bg | Interaction | `#1f5fd6` | primary actions, selection, focus |
| link | Link | `#1a4fb8` | inline links |

### Dark Mode

| Role | Name | Hex | Usage |
| --- | --- | --- | --- |
| bg | Canvas | `#121312` | page canvas |
| surface | Surface | `#1b1c1b` | panels and cards |
| elevated | Raised | `#232423` | overlays |
| fg | Foreground | `#ecece6` | body text and headings |
| muted | Muted | `#9c9d94` | secondary text and metadata |
| border | Border | `#2f312e` | hairlines |
| border-strong | Control border | `#6e7069` | control boundaries (3:1) |
| primary-bg | Interaction | `#2f62d4` | primary actions (white label) |
| focus / link | Interaction text | `#8fb4ff` | focus ring, links |

### Status and Not-real Colours

| Role | Light base / text / tint | Dark base / text / tint |
| --- | --- | --- |
| P1 critical | `#d93036` / `#b4232a` / `#fbe8e8` | `#e5484d` / `#ff8a84` / `#3a1c1d` |
| P2 warning | `#b87503` / `#8a5300` / `#fcf1dc` | `#e09b1b` / `#f5b546` / `#33280f` |
| P3 advisory | marker `#b87503`, text `#3d3f3a` | marker `#e09b1b`, text `#c4c5bd` |
| Normal | dot `#2f8a50` | dot `#6fbf8a` |
| Not real (sim / info) | `#7c5ce0` / `#5b3cc4` / `#f1edfc` | `#8b6cf0` / `#b49cff` / `#221b3a` |
| Destructive | `#b91c1c` fill, white text | `#ef4444` fill, `#101010` text |

### Accessibility derivations

Base hexes are for markers, bars, and tints; status text always uses the `-fg` token. All 49 token pairs per theme in `contrastPairs` are verified by canonical/validate.mjs (4.5:1 for text, 3:1 for control boundaries, markers, and focus).

## Color Palette

| Role | Name | Hex | Usage |
| --- | --- | --- | --- |
| background | Canvas | `#f4f4f1` | page canvas (light); dark #121312 |
| foreground | Foreground | `#1c1d1b` | body text and headings (light); dark #ecece6 |
| accent | Interaction | `#1f5fd6` | primary action (one per decision), selection, focus, links — never a status |
| surface | Surface | `#ffffff` | panels and cards (light); dark #1b1c1b |
| muted | Muted | `#5e6059` | secondary text and metadata (light); dark #9c9d94 |
| border | Border | `#dcdcd6` | hairlines (light); control boundaries use #84867e; dark #2f312e / #6e7069 |

## Typography
- **Display:** IBM Plex Sans — weights 400, 500, 600 — fallbacks: system-ui, -apple-system, Segoe UI, Roboto, sans-serif
- **Body:** IBM Plex Sans — weights 400, 500, 600 — fallbacks: system-ui, -apple-system, Segoe UI, Roboto, sans-serif
- **Mono:** IBM Plex Mono — weights 400, 500, 600 — fallbacks: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace

## Voice & Tone

- **Adjectives:** declarative, concise, technically grounded, confident, precise
- **Tone:** Senior security architect communicating with experienced operators

### Messaging pillars
- Lattix develops advanced Zero Trust, data-centric security infrastructure for enterprises, government, defense, and mission-critical environments.
- Platform provides cryptographic data protection, fine-grained policy enforcement, verifiable provenance, secure collaboration, distributed systems management, and AI data governance.
- Visual identity: minimalist, precise, technically sophisticated; neutral foundation with colour reserved for interaction, abnormal state, and simulated content.
- Core philosophy: Maximum operational capability. Minimum visual noise. Minimum cognitive overhead.

### Vocabulary
- **Use:** operational, enforcement, provenance, topology, trace, evidence, policy, subject, resource, action, decision, verification, cryptographic, distributed, governance
- **Avoid:** marketing hype, playful language, decorative copy, unnecessary jargon, generic SaaS terminology, dashboard as a catch-all, insights without evidence

## Imagery

- **Style:** Technical photography, architectural documentation, schematic diagrams, operational screenshots
- **Subjects:** Infrastructure, data centers, network topology, cryptographic hardware, mission control environments, technical documentation
- **Treatment:** High contrast, neutral, documentary aesthetic
- **Avoid:** Illustrations, abstract gradients, stock photography, decorative imagery, fictional HUD elements

## Layout

- **Radius:** 6px (4px tags/status, 8px panels and dialogs)
- **Border weight:** 1px
- **Spacing:** 4px baseline grid

### Posture rules
- Blended model: Lattix brand layer, Kumo-style component layer, ISA-101 operational status layer.
- Component kit covers: Sidebar (collapsible), Command Search, Data Table, Status Label, Inspector Panel, Policy Decision Trace, Topology Graph, Evidence Panel, Timeline, Form, Button (Primary/Secondary/Tertiary/Destructive), Toggle, Select, Breadcrumbs, Tabs, Dialog, Toast, Operating-mode frame, Priority counts.
- Primary actions: interaction blue, exactly one per decision point; secondary is the default button.
- Selection and focus: interaction colour, distinct from status.
- Status: ISA-101 priorities with shape + text; normal states are neutral (dot only); colour only when abnormal.
- Not-real states (simulated, proposal, shadow mode): violet.
- Sentence case everywhere; IBM Plex Sans for interface text, IBM Plex Mono for data.
- Controls 32px compact, 36px comfortable, 44px on touch.
- No marketing templates (landing, deck, poster, newsletter, email, form) in the operational component library.
