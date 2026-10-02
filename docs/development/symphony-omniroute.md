# OpenAI Symphony with OmniRoute

This repository runs OpenAI Symphony against the Linear **xFrontier** project (`3b160e533200`). Symphony polls eligible `FRONT-*` issues, creates isolated workspaces, and starts Codex app-server through the checked-in `WORKFLOW.md` contract.

Native Codex and OmniRoute are intentionally separate inference lanes. The OmniRoute lane uses the existing local Responses-compatible endpoint at `http://127.0.0.1:20128/v1`; it does not expose the dashboard key to repository files or child prompts.

## Setup and preflight

Copy `.env.symphony.example` to the gitignored `.env.symphony.local` and set least-privilege host-side `LINEAR_API_KEY` and `OMNIROUTE_API_KEY` values. When that file is absent, the Make targets reuse the existing sibling `lattix-monorepo/.env.symphony.local`; a repo-local file always takes precedence. The desktop Linear OAuth session cannot be borrowed by the unattended daemon.

Install/build the sibling OpenAI Symphony checkout and validate the existing OmniRoute instance:

```powershell
make symphony install elixir
make symphony-preflight omniroute
```

Preflight uses the host-side Linear credential for a read-only lookup of the exact xFrontier project and uses the OmniRoute dashboard key for `GET /v1/models`. It does not print either credential.

Start the xFrontier-bound daemon only after preflight and human review of the workflow:

```powershell
make symphony omniroute no-guards
```

Use a distinct `SYMPHONY_PORT` if another Symphony process owns port 4057. Identify the owning process before stopping anything.

## Safety boundaries

- Only `Todo` and `In Progress` issues are polled; exclusion labels and terminal states fail closed.
- `agent:eligible` is required by the execution contract before file changes.
- OmniRoute `auto` is valid only after its provider order, data handling, residency, cost, context, tool compatibility, and fallback behavior are reviewed.
- Human review remains the merge and deployment boundary. Symphony never receives production deployment authority from this workflow.
- A successful `/v1/models` preflight proves authenticated reachability, not model quality. Validate real tool calls before selecting a new route.
