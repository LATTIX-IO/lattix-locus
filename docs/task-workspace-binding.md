# Task ↔ repo binding ("where am I working")

Every inbox task / multi-agent chat session can be **bound to a specific repo**, so
the team knows where the codebase is and where to deliver the spec — the way T3
Code / Codex work inside a chosen folder. The team is **confined** to that repo
(isolated git worktree per task); touching anything outside it **requires
permission**.

## Harness support (built + tested)

`frontier_runtime/harness/workspace_binding.py`:

- **`WorkspaceBinding`** — the binding attached to a task/chat:
  `repo_path`, `base_ref`, `branch`, `isolation` (`worktree` | `in-place`),
  `allow_outside` (`ask` | `deny` | `allow`), `extra_paths` (granted outside paths),
  `test_command`. Round-trips to/from a JSON payload (`to_payload` / `from_payload`).
- **`WorkspaceManager.provision(binding, run_id)`** — creates an isolated **git
  worktree** off `base_ref` on a task branch (`frontier/<task>`), returns a bound
  `Workspace` + a `cleanup()` handle. `build_task(...)` returns a ready `SweTask`.
- **Boundary enforcement** — `LocalDirectExecutor(root, extra_paths=…)` only allows
  paths inside the repo (or explicitly granted extras); the coding toolset, on an
  out-of-bounds file op, **does not execute it** and instead returns a
  `[permission required]` message + records an escalation (`on_escalation` hook).
  Policy `deny` blocks; `allow` permits within granted paths.
- The collaborative team / SWE agent take `out_of_bounds` + `on_escalation`, so the
  whole multi-agent run honors the binding.

Run it today (CLI):
```bash
frontier-evals collaborate --repo /path/to/repo --spec @spec.md \
  --base-ref main --isolation worktree --allow-outside ask \
  --grant /path/to/shared-lib --task-id FRONT-123 \
  --test-command "pytest -q" \
  --api-base-url http://localhost:11434/v1 --model gpt-oss:20b --provider ollama
```

## Platform wiring (next — backend + inbox UI)

To make this set from the inbox "New Task" dialog and persist on the run:

1. **Run payload** — `POST /workflow-runs` accepts a `workspace` object
   (= `WorkspaceBinding.to_payload()`): `{repo_path, base_ref, branch, isolation,
   allow_outside, extra_paths, test_command}`. Store it on the run
   (`run_details[run_id]["workspace"]`) so the session is durably bound.
2. **Inbox UI** — add a **Repository** field to the New Task / chat-create form
   (`apps/frontend` inbox): a repo picker (configured repos from settings) + optional
   base ref/branch, an **isolation** toggle (worktree default), and an
   **"allow work outside this repo"** selector (ask/deny/allow) with an
   add-path control for grants. Show the bound repo as a chip on the task/chat.
3. **Execution** — when the run starts, build a `WorkspaceBinding` from the payload,
   `WorkspaceManager.provision` it, and run the team against that workspace. Surface
   `on_escalation` permission requests as **approval items** in the inbox (reuse the
   existing approval primitive); granting one adds the path to the binding and resumes.
4. **Configured repos** — a `/builder/settings` list of allowed repos (path or git
   URL) so the picker is constrained and clone-on-demand is possible for remote repos.

This binds the multi-agent architecture to a concrete working folder per task, with
git-worktree isolation and a permission boundary, and feeds the GitHub delivery
(open PR from the task branch) already built in `integrations.py`.
