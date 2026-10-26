# LOCUS-480 — Windows installed local-inference acceptance

The GitHub CI/frontend mocks do **not** prove that the actual installed Tauri
desktop routes a follow-up to a local model. This manual check is a merge/release
qualification record, not a simulated result. Do not report it as passed until
the owner runs it on the intended Windows installation.

## Prerequisites

1. Start the installed **Lattix Locus** desktop and Ollama on the owner's
   Windows host. Keep both bound to loopback. Do not paste or print API keys.
2. Use `ollama list` to choose any installed chat-capable model ID. Do not
   treat the curated model-download catalog as the live inventory.
3. Run from a local checkout of this PR:

   ```powershell
   pwsh -NoProfile -File .\scripts\windows-uat-local-inference.ps1 -ModelId 'YOUR-INSTALLED-MODEL-ID'
   ```

   The script checks Ollama `/api/tags` against Locus `/api/models/overview`
   and makes one synthetic inference directly through Ollama. It does not test
   the Locus tool gateway, session continuity or approval UX.

## Native desktop acceptance matrix

- [ ] The script passes and reports **all** installed Ollama model IDs, including
  any custom `hf.co/...` names. Embedding-only models may appear in Settings
  inventory but must not be chat-selectable.
- [ ] Settings → Engines and a **follow-up-run** composer display the same
  installed chat-capable models. No OpenAI/Anthropic credentials are required.
- [ ] Start a chat using a chosen `ollama/<exact-id>` model. Verify the run
  executes in the installed native UI and Activity records the exact provider
  and model; no hosted provider call was made.
- [ ] Continue the same session using **same-run** and **follow-up-run**
  composers. Confirm the response uses the selected local model and preserves
  context without switching to a saved preferred OpenAI provider.
- [ ] Uninstall/disconnect a local model temporarily or use an explicitly
  nonexistent ID. The model must not execute or auto-route to a hosted provider.
  Record the typed error; do not delete user data to produce the test.
- [ ] With a policy-ineligible model, Gateway/provenance denies execution.
  Confirm no fallback bypasses this denial.
- [ ] Review evidence in Activity → Runs/Traces for the successful local
  inference, a permitted retry, and the failure path. Redact session IDs,
  prompts, model outputs and credentials before sharing artifacts.

Record build version, Git commit, Windows/desktop version, Ollama version,
selected model, check results, and any sanitized failures in the issue
[LOCUS-480](https://linear.app/lattix/issue/LOCUS-480).

**Limit:** This PR's interim behavior can still disable Send when no model is
eligible. Durable, permission-aware queued Send belongs to LOCUS-479 and must
not be claimed complete here. Likewise, hosted subscription discovery and
orchestrator/worker routing are separate increments (LOCUS-484).
