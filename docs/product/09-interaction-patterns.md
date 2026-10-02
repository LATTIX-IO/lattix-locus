# 09 · Interaction Patterns

## 1. Page anatomy

Shell (left rail: spaces; top: area switcher, palette, run indicator) · main view (one job, P21) · inspector (right, on demand). No page hosts more than one primary collection or visualization.

## 2. Universal inspector

Any object (task, run, action, memory item, grant, skill, peer, board element) opens the same inspector: header (type glyph, title, status), properties (inline-editable), relations (up and down the chain: task → runs → actions → grants), provenance, history, and actions. Keyboard: `Space` to open, `Esc` to close, `[`/`]` to move through the list behind it.

## 3. Envelope card

Shown before a run starts and editable during it.

```
Goal          Draft the Bain POC follow-up pack
Done when     ✓ Pack in /Bain/POC with sources  ✓ Verification column passes
Can           Read Bain folder · Search Drive · Write drafts to /Bain/POC
Cannot        Send · Share externally · Other areas
Budget        45 min · $3.00 · 200 actions
Autonomy      Tiered (R3 asks)
Engines       Router: local → Claude Code for drafting
[Start]  [Edit]  [Save as playbook]
```

Capabilities are chips; adding one shows its risk class. The envelope is never hidden: it pins to the top of the run view.

## 4. Approval card

| Element | Content |
|---|---|
| What | The action in plain words plus the exact target ("Send email to j.doe@client.com, subject …") |
| Why | The plan step it serves and the commitment behind it, with confidence |
| Risk | R-class badge; irreversible marker; taint marker if arguments derive from untrusted content |
| Preview | Diff, rendered draft, screenshot of the target UI, or command |
| Judgments | Laya results that informed the gate (for example, "recipient matches task intent: 0.94") |
| Choices | Approve once · Approve for this run · Make standing (opens grant editor with the narrowest pattern) · Edit · Deny (with optional reason, which becomes run feedback) |

Approval cards appear inline in chat and run views, on Home, and as OS notifications for blocking requests. Shortcuts: `A` approve once, `R` for run, `E` edit, `D` deny.

## 5. Run timeline

A vertical timeline of steps; each step expands to actions. Every action row shows glyph, verb, target, risk badge, grant used, engine, duration and cost. Failed and denied actions are visible, not collapsed. A live run auto-follows unless the user scrolls.

## 6. Tracker interactions (Linear-class)

- `C` create task, `Cmd/Ctrl+Enter` save, `X` select, `Shift+click` range, `S` status, `P` priority, `A` assignee, `L` labels, `E` estimate, `D` due date, `Cmd/Ctrl+K` everything else.
- Assigning to **Locus** opens the envelope card pre-filled from the area's default playbook.
- Board drag changes status; drag between swimlanes changes the grouped field.
- Optimistic updates; local-first storage keeps every interaction under the P19 budgets.

## 7. Forms and declarative files

Skills, playbooks, policies, assemblies, triggers and engine routes have three synchronized representations: a **form** (default), the **source file** (YAML/Markdown/Rego, editable with validation), and a **diagram view** where structure matters (diagram-js). Saving from any representation validates against the same schema. Invalid states never reach a run.

## 8. Takeover HUD

An always-on-top, click-through-except-controls overlay: current step, last action, Pause (`Ctrl+Alt+Space` or move the mouse), Take over, Stop, and the panic key reminder. A coloured screen border (with a pattern, not colour alone) indicates agent control. See [12](12-computer-use.md) §5.

## 9. Provenance and tier badges

Small glyphs on answers, artifacts and memory items: `T0` code, `T1` Laya/Jev, `L` local model, `H` hosted model, `A` vendor agent (with name), `P` peer. Hover shows the engine, cost and sources.

## 10. Status and numbers

Status is glyph + word. Money and tokens use tabular numerals. Unknown values show as "—", never zero. Budgets show used/limit with a bar that changes shape (not just colour) past 80%.

## 11. Empty, loading and error states

Empty states offer one action ("Create your first area", "Pull a local model"). Errors say what failed, which component, and the next step, with a link to the failing control on the Posture page when security-related. A missing engine is an error, never a simulated answer (P16).

## 12. Typography, density and accessibility

14 px body, 12 px floor, sans for language, mono/tabular for numbers and identifiers. Light and dark themes are equal. WCAG 2.2 AA on primary flows. Every workflow keyboard-operable; the HUD and approval cards are screen-reader announced.
