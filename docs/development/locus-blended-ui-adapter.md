# Locus blended UI design integration (increment 1)

The v0.3.1 Open Design prototype is a **reference** for operator journeys and product-specific Lattix design rules, not a replacement runtime, React app, authorization implementation, or pixel-perfect specification.

## First bounded integration
- Semantic warm-neutral palette, blue interaction, separate operational status colours, 4/6/8 px component radii.
- IBM Plex Sans/Mono fonts self-hosted and distributed under SIL OFL 1.1. No build-time Google Fonts dependency.
- The existing shadcn/Radix HSL token API and Next.js shell stay in place.
- A reusable status primitive renders a shape and word; the database health indicator uses the real platform health response. No simulated metrics.
- Touch controls receive 44px minimum target size on coarse pointers.

## Navigation reconciliation
The approved reference shows seven areas: Home, Work, Activity, Library, Memory, Control Tower, Settings. The existing production shell currently exposes five real areas: Home, Activity, Memory, Library, Settings.

**Do not add dead/duplicate navigation.** Work requires a persistent task/project/schedule contract and functioning routes; Control Tower requires authoritative worker, model, policy, and service telemetry. Those become first-class navigation only when a functioning route, non-placeholder data, authorization, empty/error/retry states and responsive tests exist. Until then, retain the five functional production areas and existing redirects. Desktop local-first authentication is not replaced by the hosted Northwind sample session.

## Next increments (separate PRs)
1. Reconcile TaskIntent / TaskPlan / RunAttempt contracts with existing backend types, stream terminal semantics, native widening confirmation and signed audit receipts.
2. Wire Home -> reviewed Plan -> backend Run -> Activity -> Outcome/Memory, with negative security and reconnection tests.
3. Integrate governed agent and team configuration with canonical AgentDefinition; preserve graph/guardrail/memory fields.
4. Add Work and Control Tower only with real endpoints; no simulated policy decisions or dashboard figures.

## Acceptance
Run `cd apps/frontend && npm run lint && npm test && npm run build`; verify light/dark themes, keyboard focus and 320/390/640/768/1280 viewports. Run API integration/negative tests before describing any security posture as enforced.
