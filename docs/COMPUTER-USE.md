# Computer use (v1)

Status of LOCUS-341, the first slice of [docs/product/12-computer-use.md](product/12-computer-use.md) (D-06). This page covers what ships, how it is contained, and what has been verified.

## What it can do

| Surface | Tools (model-facing) | Gateway action kinds | Implementation |
|---|---|---|---|
| Agent browser | `browser_navigate`, `browser_read`, `browser_act` (click / fill / press / select), `browser_screenshot` | `browser_navigate`, `browser_read`, `browser_act` | Playwright for Python + Chromium (`locus_runtime/computer_use/browser.py`) |
| Windows desktop | `desktop_observe`, `desktop_click`, `desktop_type`, `desktop_key` | `ui_observe`, `ui_click`, `ui_type`, `ui_key` | UI Automation COM through `comtypes` (`windows_uia.py`) |
| macOS desktop | same desktop tools | same | Accessibility (AX) API through PyObjC (`macos_ax.py`), **unverified on a real Mac** |

`ComputerUseToolset` (`locus_runtime/computer_use/toolset.py`) adds these tools to the verified loop's `CodingToolset`. The run envelope's `capabilities.tools` picks what the model is offered, so a run can use them alongside the coding tools or instead of them. `capabilities.apps` lists the desktop apps the run may drive. `RunEnvelope.gateway_capabilities()` turns the tools into gateway operations (`computer_use_operations`). Browser tools also need `network_egress`, because the browser authorizes every request host.

## Modes and human control

`ComputerUseController` (`controller.py`) holds one process-wide mode and the panic latch. Every tool shares it.

| Mode | Behaviour |
|---|---|
| `observe` (default) | Only `ui_observe` / `browser_read` run. Acting calls return `[blocked]`. |
| `assist` | Acting calls come back as proposals with their risk class. Nothing is driven and the gateway is not asked, so no `allow` is ever recorded for an action nobody took. |
| `takeover` | The agent drives input, still gated action by action. |

**Panic.** `controller.panic()` cancels every in-flight action's `CancelToken` and refuses new actions until a human calls `reset()`. After a reset the mode drops back to `observe`. Panic is idempotent, and it never waits on the tools: listeners only set flags. The tools call `token.check()` before every primitive: each key, each chunk of text, and each 25 ms poll while waiting for an element or a page load. Panic also closes the agent browser on the browser's next call.

- Measured on Windows 11: in-flight synthetic typing (fake backend, 2,000 keystrokes queued) stopped within 0.22 ms of panic at worst over 20 runs (median 0.11 ms). The test in `tests/unit/test_computer_use_controller.py` asserts at most 100 ms. An in-flight Chromium action waiting on an element stopped 1.5 ms after panic (`tests/policy/test_computer_use_opa.py`). New actions are rejected in under 0.1 ms. A single Playwright primitive that is already running (a click, a fill) is bounded by its own action timeout, 5 s by default; cancellation takes effect at the next primitive boundary.
- `POST /computer-use/panic` exposes panic. It always requires authentication, whatever the runtime profile. It is idempotent and audited as `computer_use.panic`. `GET /computer-use/status` and `POST /computer-use/reset` sit beside it.
- **Follow-up:** wire the desktop app's global hotkey to `POST /computer-use/panic`. This needs `tauri-plugin-global-shortcut` in `apps/desktop-tauri` plus the backend bearer token. It is not done here because it cannot be built and verified in this change.
- Not in v1: the OS-level native helper (12 §4) with physical-input preemption, HUD and screen border. Cancellation is cooperative and runs inside the Python process.

## Safety model

**One gateway (P6).** Every call is a `GatewayAction` authorized before it runs, and it is audited. The gateway classifies the action from the `UiFacts` the tool perceived immediately before acting: the live DOM node or UIA/AX element. It never uses model claims. It recomputes the class and never lowers it.

| Condition | Class |
|---|---|
| observe / read | R0 |
| screenshot (stored frame) | R1 |
| navigate (the host must also pass `network_egress`) | R2 |
| other click / fill / type / press / select / key | R2 |
| activating a control whose text says send, submit, pay, buy, delete, confirm, transfer, … (also `_R3_VERBS`) | R3 |
| submitting a form that holds a password, payment or OTP field, or whose submit control says so | R3 |
| Win / Cmd / Meta key chords (they reach the OS shell) | R3 |
| fill / type / press / key / select into a password, card number, CVV, SSN or OTP field (type, `autocomplete`, UIA `IsPassword`, AX secure text field, or the field's name or label) | **R4: always denied, never grantable** |
| missing facts or an unknown control | R4 (fail closed) |

**Policy (`policies/computer_use.rego`).**

- Desktop actions need the app (executable or bundle id) on the run's allowlist.
- A built-in deny list always wins. It covers password managers, banking, payment and crypto apps, OS security tools and credential prompts (UAC `consent.exe`, `CredentialUIBroker`, LogonUI, Keychain, SecurityAgent, System Settings), shells and terminals (typing into them would bypass `process_exec` and the jail), the user's own browsers (agent browser only; using the user's own profile is an H2 capability), and Locus itself.
- `LOCUS_COMPUTER_USE_DENIED_APPS` (comma-separated) and the run's `denied_apps` only add to the list.
- Navigation must be `http(s)` to a non-empty host, so `file:`, `javascript:` and `data:` are refused.
- Data entry into secret fields is denied here too, as defence in depth.

**Taint (13 §6, P8).** All screen and page text is untrusted.

- Read output is wrapped in an `<<untrusted-content NONCE>>` block with a fresh random nonce, so page content cannot close the block early.
- Element text can only *raise* a class. A page that labels its Delete button "OK" gets the default R2, never less. That is the residual risk of label-based classification. Submit-in-sensitive-form detection and the R4 field checks do not depend on labels alone.
- Computer-use actions are always tainted. The taint gate (`gateway.taint_gate_grant_not_applicable`) skips standing grants for them, so screen text can never be what turns an `ask` into `allow`. Only a single-use human approval of the exact action can, bound to the action fingerprint, which includes the perceived element.
- Computer-use code never creates grants.

**Agent browser.**

- It runs a dedicated persistent profile at `<app_home>/computer_use/agent-browser-profile` (`LOCUS_APP_HOME` overrides the home). Paths inside real Chrome, Edge, Brave or Firefox profile roots are refused.
- It runs headless by default; `headless=False` shows the window.
- Downloads are refused, service workers are blocked, and non-proxied WebRTC UDP is disabled.

**Egress.**

1. The navigation host must pass `network_egress`.
2. Every request is intercepted (`BrowserContext.route`, plus `route_web_socket`). Hosts the gateway does not allow are aborted and audited as `network_egress` denials.
3. Playwright interception does not see redirect hops (verified), so the browser's only route out is a loopback `EgressProxy`. Loopback traffic is not bypassed. The proxy authorizes every connection by host (CONNECT or absolute-URI HTTP) through the gateway before any byte goes upstream.

The allowlist is host-level, not port-level.

**Desktop.**

- Element identity and coordinates are re-read live before each action. An element from another process is refused.
- Semantic patterns are used first: UIA Invoke, Toggle, SelectionItem and Value; AX press and set-value.
- Synthetic input (`SendInput` / `CGEvent`) is a fallback only. It is sent while the target app owns the foreground window, re-checked before every character, and refused if another window covers the click point.
- `allow_synthetic_input=False` disables the fallback.
- Password-field values are never read.

**Frames.**

- `browser_screenshot` masks password, payment-card, CVV, SSN and OTP inputs with opaque boxes. It writes the PNG under `<run_dir>/frames/` with a JSON sidecar: created, expiry (default 14 days; `LOCUS_COMPUTER_USE_FRAME_RETENTION_DAYS`), host only (never the query string), masked count, SHA-256 and the gateway audit id.
- `prune_expired_frames()` deletes expired frames.
- Frames are **not encrypted at rest** yet (12 §6 asks for that). Follow-up.

## What has been verified, and where

| Claim | Evidence |
|---|---|
| Risk table, R4 secret fields, taint gate vs. grants, exact-approval unlock | `tests/unit/test_computer_use_gateway.py` |
| App allow / deny lists, navigation scheme, secret-field deny (Rego) | `policies/tests/computer_use_test.rego`, parity cases in `tests/policy/test_policy_parity.py` |
| Real Chromium. Navigate, read, fill, click, select. Password value never in read output. Password and card typing denied even with an all-covering grant and a human approval. "Pay now" asks and runs only after an exact approval. An injected on-page instruction cannot escalate a Delete click. Off-allowlist navigation and `file:` denied. Off-allowlist subresource aborted and audited. A redirect to an off-allowlist host is blocked by the proxy. Screenshot masks the password box. Modes. Panic latency. | `tests/policy/test_computer_use_opa.py` (real OPA, local HTTP server on 127.0.0.1) |
| Real Windows UIA: observe a Notepad window and set its text through the Value pattern, then restore it | `tests/policy/test_computer_use_opa.py::test_real_notepad_observe_and_type`. It skips without a desktop session, and also when Notepad is already running, so the user's documents are never touched. |
| macOS AX backend logic (secure fields, walk caps, frontmost check) | `tests/unit/test_computer_use_desktop.py`, with a **fake** PyObjC API only. **Not verified on a Mac.** |
| Panic endpoint is authenticated, idempotent, cancels and latches; posture control | `apps/backend/tests/test_computer_use_endpoint.py` |

**Posture.** The `computer_use` control (`apps/backend/app/control_status.py`) is `enforced` only when a controller is installed (`install_controller`) *and* an enforcing gateway is installed. It is `unverified` with the controller but no gateway, and `off` otherwise. The backend does not install a controller yet: no backend run drives computer use. So the control reports `off` today.

## Dependencies (P28 / P29 / P30)

| Package | Version | Licence | Provenance and why |
|---|---|---|---|
| `playwright` | 1.63.0 | Apache-2.0 | Microsoft. The maintained FOSS Chromium driver with accessibility-aware locators. Transitive dependencies: `greenlet` (MIT), `pyee` (MIT). Chromium comes from Microsoft's Playwright CDN (`python -m playwright install chromium`). |
| `comtypes` | 1.4.17 (Windows) | MIT | The community `enthought/comtypes` project. Calls `UIAutomationCore` directly. `uiautomation` and `pywinauto` were rejected under P28 (maintainer provenance). |
| `pyobjc-framework-ApplicationServices` | 12.2.2 (macOS) | MIT | PyObjC (Ronald Oussoren). The standard Python bridge to the AX API. |

The egress proxy is our own code, about 150 lines. mitmproxy (MIT) was considered, but full TLS interception and a CA are far more than a host allowlist needs.

## Follow-ups

- Desktop-app global hotkey → `POST /computer-use/panic` (Tauri global-shortcut plugin).
- Signed native helper (12 §4): OS-level preemption on physical input, HUD and screen border, panic outside the Python process.
- Encrypted frame storage. Sensitive-screen detection that drops whole frames rather than masking fields.
- Per-app R3 standing-grant override of the built-in deny list (12 §6). v1 does not allow overrides at all.
- Run the scenario suite on a real Mac before claiming macOS (12 §9).
- PyInstaller packaging of Playwright's driver for the desktop bundle (`packaging/locus-backend.spec`).
- Wire a computer-use run into the backend (install the controller), which flips the posture control.
