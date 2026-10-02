# Fix: Agent Studio (and all builder lists) show mock data instead of real agents

## Symptom
The Agent Studio lists three agents — *Orchestration Agent v5*, *Market Intelligence
Agent v4*, *Outreach Critic v2* — regardless of backend state. Newly seeded/published
agents (e.g. **Full-Stack SDET Agent**) never appear.

## Root cause (diagnosed 2026-06-12)
Those three are **mock data** (`apps/frontend/src/lib/mock-data.ts:150-152`). The frontend
falls back to mock data when its backend API call fails — and it is failing with **401**.

- The backend requires authentication (`require_authenticated_requests` is true) and grants
  builder access from an **operator session token**, read from the `frontier_operator_session`
  cookie (`_request_operator_session_token`, `main.py:1133`).
- The frontend API client (`apps/frontend/src/lib/api.ts`) only attaches an `x-frontier-actor`
  header (and only when `NEXT_PUBLIC_FRONTIER_ACTOR` is set — it is empty). It never forwards
  the casdoor operator session cookie or an `Authorization` bearer to `backend:8000`.
- Result: every server-side call to the backend is anonymous → `GET /agent-definitions` → 401
  → UI shows mock agents.

Confirmed: the backend store holds 5 published `graph` agents incl. `Full-Stack SDET Agent`
(`frontier_state_store` → `section:agent_definitions`); `GET /agent-definitions` returns all
definitions unfiltered. So this is purely a frontend→backend auth-forwarding gap.

## Fix (recommended): forward the operator session to the backend

In the frontend's **server-side** fetch path (`api.ts` `getApiBase()` returns the internal
URL when `typeof window === "undefined"`), forward the incoming request's operator session as
a bearer. In a Next.js server context, read the cookie via `next/headers`:

```ts
// server-only helper
import { cookies } from "next/headers";

function getAuthHeaders(): Record<string, string> {
  if (typeof window !== "undefined") return {};            // never from the browser
  const token = cookies().get("frontier_operator_session")?.value;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
```

Merge `getAuthHeaders()` into the request headers alongside `getRequestIdentityHeaders()`.
The backend already accepts this token (`_enforce_request_authn` → `_request_operator_session_token`
→ `_decode_operator_bearer_token`). No posture change: auth stays required; the logged-in
user's own identity simply reaches the backend.

> Note: data-fetching functions must run in a server context that has request cookies
> (Server Component / route handler / server action). If any list is fetched in a shared
> context, route it through a server action or a `/api/*` Next route handler that forwards
> `Authorization`.

### Apply
```bash
# 1) edit api.ts as above
# 2) rebuild the frontend image (code + NEXT_PUBLIC are baked at build time)
docker compose build frontend && docker compose up -d frontend
```

## Alternatives (local dev only)
- **Static bearer**: set `FRONTIER_API_BEARER_TOKEN=<dev-token>` on the backend and have the
  server-side `api.ts` attach `Authorization: Bearer ${process.env.FRONTIER_API_BEARER_TOKEN}`.
  Simpler, but introduces a shared dev secret (keep it out of committed files). Still needs a
  frontend rebuild.
- **Header-actor auth**: only works when auth is *not* required and the profile is
  `local-lightweight` (`_header_actor_auth_allowed`, `main.py:8729`) — i.e. it *lowers* the
  security posture. Not recommended.

## Verify after rebuild
```bash
# backend log should show 200 (not 401) for /agent-definitions, and the UI shows
# Full-Stack SDET Agent (graph, published, v1) as the 4th agent.
docker logs --tail 20 lattix-xfrontier-backend-1 | grep agent-definitions
```
