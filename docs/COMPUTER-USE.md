# Computer use (v1)

Status of LOCUS-341, the first slice of [docs/product/12-computer-use.md](product/12-computer-use.md) (D-06), and LOCUS-350, the principal's own browser profiles (D-25). This page covers what ships, how it is contained, and what has been verified.

## What it can do

| Surface | Tools (model-facing) | Gateway action kinds | Implementation |
|---|---|---|---|
| Agent browser | `browser_navigate`, `browser_read`, `browser_act` (click / fill / press / select), `browser_screenshot` | `browser_navigate`, `browser_read`, `browser_act` | Playwright for Python + Chromium (`locus_runtime/computer_use/browser.py`) |
| Your own browser (LOCUS-350) | `user_browser_tabs`, `user_browser_observe`, `user_browser_navigate`, `user_browser_act` (click / fill / press / select / scroll), `user_browser_screenshot` | `user_browser_read`, `user_browser_navigate`, `user_browser_act` | Locus WebExtension + native-messaging host (`apps/browser-extension/`, `locus_runtime/computer_use/user_browser/`); see [Your own browser](#your-own-browser-locus-350-d-25) |
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
- **Desktop panic hotkey (LOCUS-346).** The desktop app registers a global shortcut, **Ctrl+Alt+Shift+Esc** (macOS: **Cmd+Alt+Shift+Esc**), that `POST`s `/computer-use/panic` on `127.0.0.1:8000`. Set `LOCUS_PANIC_HOTKEY` (for example `ctrl+alt+shift+F12`) in the app's environment to choose another chord. The hotkey uses `tauri-plugin-global-shortcut` v2 and is handled in Rust (`apps/desktop-tauri/src-tauri/src/computer_use.rs`) on its own thread with a raw loopback request, so it fires even when the UI webview is frozen or still on the loading page. Authentication: the desktop backend runs the `local-native` profile with the local-operator bootstrap (loopback only), the same path the UI uses. If `LOCUS_API_BEARER_TOKEN` is set in the shell's own environment it is sent as a bearer token too. The token is never logged. If another app already owns the chord, registration fails, the shell logs it, and the endpoint still works.
- **Takeover indicator.** The shell polls `GET /computer-use/status` about once a second. While the mode is `takeover`, the tray tooltip reads "AGENT IS CONTROLLING THIS COMPUTER (... to stop)" and, on macOS, the menu bar shows "AGENT IN CONTROL". After a panic it reads "computer use stopped (panic)". A richer web UI indicator is P31.
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
- A built-in deny list always wins. It covers password managers, banking, payment and crypto apps, OS security tools and credential prompts (UAC `consent.exe`, `CredentialUIBroker`, LogonUI, Keychain, SecurityAgent, System Settings), shells and terminals (typing into them would bypass `process_exec` and the jail), the user's own browsers (desktop UIA / AX never drives them; the principal's own profiles are reached only through the Locus extension, below), and Locus itself.
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
| Backend installs the controller only with an enforcing gateway, and posture then reads `enforced`. Run input `computer_use` reaches the session capabilities and the code node. A token-less loopback panic is accepted only under the desktop local-operator bootstrap. | `apps/backend/tests/test_computer_use_backend_wiring.py` |
| Envelope tools select the `ComputerUseToolset` (SweAgent, loop runner), and the browser is released at the end of the run | `tests/unit/test_computer_use_wiring.py`, `tests/harness/test_loop_runner.py` |
| First-run Chromium install command (bundled driver, app-home browsers path, no host override, once per version). Spec, Cargo and capability wiring checked as strings. | `tests/backend/test_desktop_firstrun.py`, `tests/backend/test_desktop_packaging.py`. **No PyInstaller, Tauri or `cargo` build was run.** |

**Posture.** The `computer_use` control (`apps/backend/app/control_status.py`) is `enforced` only when a controller is installed (`install_controller`) *and* an enforcing gateway is installed. It is `unverified` with the controller but no gateway, and `off` otherwise. At startup the backend installs the process controller (`policy_gateway.ensure_computer_use_controller`) right after the gateway, but only when that gateway is enforcing (OPA running). With OPA down nothing is installed, runs get no computer-use tools, and the control reports `off`.

## Running computer use (LOCUS-346)

A run gets the computer-use tools when its envelope lists them (`capabilities.tools`) and a controller is installed. Otherwise it keeps the coding tools only, and the omission is logged.

- **Backend workflow runs.** Pass `"computer_use": {"tools": ["browser_navigate", "browser_read", "browser_act"], "apps": ["notepad.exe"]}` in the run input. Unknown tool names are dropped, and apps are bounded strings (`policy_gateway.computer_use_request`). The run's harness gateway session then grants those tools' operations (`ui_*`, `browser_*`, `network_egress`) and the listed apps. Code nodes and the team implementer get a `ComputerUseToolset` (`locus_runtime/computer_use/wiring.py`). Analyzer nodes stay read and exec only. Browser egress is still the operator allowlist.
- **Linear loop runner.** It builds its toolset through the same wiring from the envelope's tools, on the session the envelope's capabilities opened. Its default envelope lists coding tools only.
- **Mode.** The controller starts in `observe`. Acting tools return proposals until the mode is `takeover`, and a panic resets the mode to `observe`.
- **Desktop bundle.** The PyInstaller spec ships Playwright's Node driver (`collect_all("playwright")`). First run (`locus_tooling/desktop_firstrun.ensure_playwright_chromium`) runs that driver's `install chromium` into `<app_home>/playwright`, which is the same as `python -m playwright install chromium`. The supervisor exports `PLAYWRIGHT_BROWSERS_PATH` to that directory before the backend starts. The Chromium build is the one pinned by the `playwright` package version (1.63.0). It comes from Playwright's CDN with the headless shell and small helpers (ffmpeg, and winldd on Windows). Download-host overrides are stripped. A per-version marker makes later launches a no-op. If the install fails, browser tools report themselves unavailable and nothing else is affected.

## Your own browser (LOCUS-350, D-25)

The agent can work in the principal's **own signed-in** Chrome, Edge and Firefox profiles, under a browser autonomy tier the principal chooses. This is separate from the isolated agent browser above. A run gets it when its envelope lists the `user_browser_*` tools.

### Mechanism, and the alternatives considered (P30)

| Option | Verdict |
|---|---|
| CDP attach to the real profile (`--remote-debugging-port`) | **Not viable.** Chrome 136+ ignores the flag on the default user-data-dir. Any process on the machine with the port would also get full control of every signed-in session. |
| Chrome's "Allow remote debugging for this browser instance" toggle (`chrome://inspect`) | **Rejected.** It is Chromium-only, and it hands full CDP control of every tab to any local process. The gateway would see no element facts. |
| Playwright MCP extension mode / "Playwright MCP Bridge" (Microsoft, Apache-2.0) | **Rejected for this use.** The licence and provenance are fine. But it is Chromium-only: it uses `chrome.debugger`, which Firefox lacks. It relays **raw CDP** for a tab to a local MCP server (a Node process) over a WebSocket. Authorization is either a per-connection approval dialog or a token copied out of the extension UI into the MCP config, so a plaintext token sits outside the OS secret store. The gateway would only see MCP tool calls, not the element facts the risk classes depend on, and the extension cannot enforce our floor (secret fields, element digests, panic epochs). It also shows the "is debugging this browser" infobar. It remains the right tool for a developer driving their own browser from an MCP client. |
| **A minimal Locus WebExtension + native-messaging host** | **Chosen.** One Manifest V3 codebase for Chrome, Edge and Firefox. The extension exposes a handful of primitives and enforces part of the floor itself. Native messaging lets the browser start our host only for the pinned extension ID. The host holds the pairing key from the OS secret store and relays over loopback, so the extension never holds the key. |

```
model ─▶ ComputerUseToolset ─▶ BrowserDriver port ─▶ UserBrowserDriver ─▶ gateway (user_browser policy + risk class)
                                                                           │ allow only
                                                                           ▼
       Locus extension ◀─ stdio ─▶ native host (locus-backend) ◀─ loopback HTTP + pairing key ─▶ RelayHub (backend)
```

- **Extension** (`apps/browser-extension/`): `manifest.json`, `background.js`, `content.js`, `popup.html/js`. It uses `browser ?? chrome` and declares both `background.service_worker` (Chromium) and `background.scripts` (Firefox). Primitives: list tabs, tab info, observe (elements with refs plus visible text), inspect (classification facts and a digest), navigate (http(s) only), act (click / fill / press / select / scroll), and a screenshot of the visible tab with secret fields masked. Element refs live in the content script's isolated world (a `WeakRef` map), never in the DOM, so page scripts cannot see or forge them. The extension executes only commands relayed from the backend and never acts on its own initiative.
- **Native host** (`locus_runtime/computer_use/user_browser/native_host.py`). The host manifest points straight at the frozen `locus-backend` binary. `desktop_main` recognises a browser launch from its arguments (Chromium passes the extension origin; Firefox passes the add-on ID) and runs the host instead of the app. From a checkout it runs as `python -m locus_runtime.computer_use.user_browser.native_host`. The host refuses unpinned origins. It reads the pairing key from the secret store and talks only to a loopback `http` backend (`LOCUS_USER_BROWSER_BACKEND_URL`, default `http://127.0.0.1:8000`). It forwards backend commands to the extension and results to the backend. The only extension event it forwards is the popup's panic button.
- **Relay** (`relay.py`, `RelayHub`) is in the backend process. It holds per-client bounded queues and pending results. A host is admitted (`POST /user-browser/relay/hello`) only with a pinned origin and the pairing key, compared in constant time. It then gets a per-connection session token, and only the token's hash is kept.
- **Driver** (`driver.py`, `UserBrowserDriver`) is the `"user"` implementation of the `BrowserDriver` port (see "Browser port" below).

### Pairing and trust chain

1. The browser starts `io.lattix.locus_browser` only for the pinned ID in the host manifest. Chromium uses `allowed_origins: ["chrome-extension://mbponpdpopkfhjehjnbgackahejhidkn/"]`, which the manifest `key` derives. Firefox uses `allowed_extensions: ["locus-browser@lattix.io"]`. Only the public half of the Chromium key is in the repo.
2. **Pairing.** The principal calls `POST /user-browser/pairing`. That creates (or rotates) a random per-install key stored only through `locus_tooling.native_secrets`: Keychain, Credential Manager or DPAPI. The key is never returned, logged, or stored in the extension. `DELETE /user-browser/pairing` deletes the key and drops every live session from its next request.
3. The relay refuses a request unless it comes from loopback with no browser headers (`Origin`, `Sec-Fetch-*`, `Referer`), so a web page cannot call it even on loopback. It also refuses unpaired installs, mismatched keys, unpinned origins and unknown sessions. Every refusal is audited.
4. **Registration.** `locus_tooling/native_messaging.py` writes per-user host manifests and, on Windows, the `HKCU\Software\{Google\Chrome,Microsoft\Edge,Mozilla}\NativeMessagingHosts\io.lattix.locus_browser` keys. It never writes HKLM. The registry writer must be passed explicitly (`WinRegistry` in the installer). Tests use a fake writer and temp directories. Registration runs only from the installer or first-run, when the principal chooses to connect a browser. **Not yet wired into the installer UI** (follow-up).

### Tiers (principal-only) and the floor

The tier is set only by a human principal with `PUT /user-browser/tier` (`{"tier", "allowlisted_sites", "granted_sites", "acknowledge_risk"}`). Agent tokens, services and internal callers are refused, even if they are configured as admins. The tier lives in the backend's `TierStore` (persisted under `<app_home>/computer_use/user-browser-tier.json` on the desktop install). The gateway reads it for every action (`gateway.user_browser_input`). It is never part of an action, a run envelope or a tool argument, so neither the agent nor page content can change it.

Widening beyond Strict needs `acknowledge_risk: true`. That records **informed consent**: who, when, the tier, and the risk text shown (`TIER_RISKS`). A widened tier without a matching consent record behaves as Strict. Adding sites to a list counts as widening again. Narrowing never needs consent, and going back to Strict clears it. The Posture page shows the `user_browser` control as `enforced` in Strict and `degraded` above it, with who accepted the tier and when (P32).

Sites are registrable domains (eTLD+1) from the Public Suffix List (`tldextract`, using its bundled snapshot with no network fetch and private suffixes on, so `alice.github.io` is its own site). A list entry covers the site and its subdomains, and nothing that merely looks similar.

| Action (site in the tier's list, tab shared) | Strict (default) | Assisted | Trusted | Open |
|---|---|---|---|---|
| list tabs | shared tabs only | + allowlisted sites | + allowlisted / granted sites | all tabs |
| observe / screenshot / scroll | shared tabs only (else **deny**) | allow | allow | allow |
| navigate | ask | allow | allow | allow |
| click / fill / press / select (R2) | ask | ask | allow | allow |
| irreversible (R3): send, delete, confirm | ask | ask | ask | allow (consent covers it) |
| payment or purchase (pay, buy, order, checkout, subscribe, billing, submitting a form with a card or secret field) and account-security changes (password, passkey, 2FA, recovery, security, permissions) | ask | ask | ask | **ask** (no tier covers it) |
| type into a password / card / CVV / SSN / OTP field (R4) | **deny** | **deny** | **deny** | **deny** |

A site outside a tier's lists falls back to the next lower tier's rules. Below Open, navigating a tab the principal did not share asks. A tab the agent opened itself counts as shared. "Ask" means a single-use human approval of that exact action (`ApprovalLedger`). Standing grants never apply, because page-derived actions are always tainted (P8).

We read Strict's "every action asks" together with "observe only shared tabs". Sharing a tab from the extension popup is the human's consent to reading it, so reads of a shared tab do not ask, and reads of any other tab are denied.

**The floor, in every tier, cannot be removed by a tier or a grant:**

- **Gateway mediation and audit (P6/P11).** Every action is a `user_browser_read` / `user_browser_navigate` / `user_browser_act` gateway action, and the run's envelope must list it (`agent_policy`). The `user_browser` Rego policy decides from the tier and the floor. The gateway adds the risk class: R4 is always denied, and R3 asks unless the Open tier's consent covers it. Unknown or malformed policy outputs fail closed: the action asks and there is no Open consent.
- **Panic.** Every action runs inside the shared `ComputerUseController`. A panic (`POST /computer-use/panic`, the desktop hotkey, or the extension popup's **Stop** button) cancels in-flight calls at once. The test bound is 100 ms. Panic also fails every pending relay call and sends `panic` to every extension. It bumps the command epoch, so the extension refuses any command issued before the panic. The policy denies while the latch is set.
- **Taint (P8).** Page content is wrapped as untrusted data. It cannot widen grants or change the tier, because the tier is not an input the page or the agent can reach.
- **Secrets (P10).** The extension never returns the value of a password, card, CVV, SSN or one-time-code field. The driver re-redacts anything that looks like one. Entering data into such a field is R4 at the gateway, a deny in Rego, and refused by the extension itself even if the backend were bypassed. Saved logins and autofill stay with the browser and the human. The model never reads or types them.
- **TOCTOU.** An act carries the digest of the element facts the gateway authorized. The extension refuses with `element_changed` if the element differs when it acts.
- **No paired extension means no action** (`not_paired`).

### Browser port (D-28)

`locus_runtime/computer_use/browser_contract.py` defines the `BrowserDriver` protocol, the Pydantic `BrowserAction` / `BrowserObservation` / `ElementRef` models and `PORT_VERSION = "1.0"`. `AgentBrowser` (`profile="agent"`) and `UserBrowserDriver` (`profile="user"`) both implement `perform(BrowserAction) -> BrowserObservation`. `drivers.build_browser_drivers` is the only factory, and it picks the drivers from the envelope's tools. The toolset maps every browser tool to one port action through `operations.BROWSER_TOOL_PORT`. Tiers and the floor stay in Rego and the gateway, never in a driver. `tests/policy/test_browser_driver_contract.py` runs one parametrized contract suite against both drivers: gateway-allowed actions only, panic stops and latches, secret values never observed or typed, observe mode refuses acts.

### Per-browser setup

The backend must be running and the browser paired (`POST /user-browser/pairing`), and the host manifest must be registered (installer step). Then:

- **Chrome / Edge.** Open `chrome://extensions` or `edge://extensions`, turn on Developer mode, choose **Load unpacked**, and select `apps/browser-extension` (or its copy in the install). The manifest `key` pins the ID to `mbponpdpopkfhjehjnbgackahejhidkn` in both browsers, matching the host manifest. A store-published build gets the store's ID. That ID must be added to `pairing.ALLOWED_ORIGINS` and the host manifest, which is a reviewed code change.
- **Firefox.** For development, use `about:debugging` > This Firefox > **Load Temporary Add-on** and pick `manifest.json`. It stays until Firefox restarts. Release builds need a **signed XPI**: AMO *unlisted* signing (`web-ext sign --channel=unlisted`) keeps the add-on off the public listing. The add-on ID `locus-browser@lattix.io` must stay the same. In `about:addons` > Locus > Permissions, check that site access is granted (Firefox can make MV3 host permissions optional).
- **Use.** Click the Locus toolbar icon. **Share this tab with Locus** marks the current tab as shared (Strict reads only shared tabs). **Stop the agent (panic)** latches panic for every computer-use tool. The popup also shows whether the host is connected.

### Desktop: out-of-band confirmation for widening

On the desktop install, the local-operator bootstrap authenticates any loopback request as the operator. Without an extra check, an un-jailed local process could widen the tier itself. So on the desktop profile (`local-native`, and wherever a shell secret was handed over), **widening needs a proof that only the Tauri shell can produce, after the human confirms a native OS dialog.** Widening means any of: a higher tier than the effective one, any new allowlisted or granted site, or pairing a browser.

1. **Secret.** At sidecar spawn the shell draws a 32-byte secret from the OS CSPRNG (`getrandom`). It writes the secret to the backend's **stdin** as one line, and sets the flag `LOCUS_SHELL_CONFIRMATION=stdin` (the flag is not the secret). The secret never goes into the environment, argv, a file or a log. The frozen backend (`desktop_main`) reads it once, before the supervisor starts any child (`locus_tooling/shell_confirmation.py`). It keeps the secret in memory only, then points fd 0 and the Windows standard input handle at the null device, so no child process inherits the pipe.
2. **Dialog.** The UI invokes the Tauri command `confirm_browser_tier` (`{tier, allowlistedSites, grantedSites}`; both lists required) or `confirm_browser_pairing`. The shell shows a native dialog (`tauri-plugin-dialog`) with the exact risk text (`TIER_RISKS`, mirrored in `browser_tier.rs` and checked by a test) and the full site lists.
3. **Proof.** Only on **Allow** does the shell sign HMAC-SHA256(secret, canonical request) with a fresh 16-byte nonce and a timestamp, and send the request itself with `X-Locus-Shell-Proof: v1:<ts>:<nonce>:<hmac>`. The canonical messages are `locus-shell-proof/v1|browser-tier|<tier>|<allowlisted,...>|<granted,...>|<nonce>|<ts>` and `locus-shell-proof/v1|browser-pair|<nonce>|<ts>`. The webview never sees the secret or the proof.
4. **Backend checks.** The backend recomputes the proof over the received request. It refuses with **403** and an audit event when the proof is missing, malformed, signed for another request, expired (more than 60 s off), replayed (each nonce is single-use) or when no shell secret exists. The check runs under the tier store's lock, so a concurrent change cannot turn an unproven request into a widening.

Narrowing (down the tiers, removing sites, back to Strict, unpairing) never needs a proof. On non-desktop profiles there is no shell: principal auth plus `acknowledge_risk` remains the rule. A desktop backend started without the shell (for example `lattix native-up`) has no secret, so it refuses widening altogether (fail closed).

The webview gets no `dialog:*` permission, so a page cannot open native dialogs that look like this confirmation. The Rust side calls the dialog plugin directly.

### Endpoints

| Route | Who |
|---|---|
| `GET /user-browser/status`, `GET /user-browser/tier` | authenticated |
| `POST` / `DELETE /user-browser/pairing`, `PUT /user-browser/tier` | the human principal only (admin, `principal_type=user`, no agent / service / internal caller). Cross-site browser requests are refused: `Sec-Fetch-Site: cross-site`, or an `Origin` outside the CORS allowlist. On the desktop, pairing and any widening also need the shell's proof (above). |
| `POST /user-browser/relay/{hello,next,result,event,bye}` | the native host only: loopback, no browser headers, pairing key, then session token |

### Residual risks

- **An extension with tab access is powerful.** `<all_urls>` host access is needed to read pages and capture the visible tab. If our extension or its update channel were compromised, every signed-in session would be exposed. Mitigations: minimal code, no remote code, a strict extension-page CSP, a pinned ID, and the relay refusing anything without the pairing key. The extension itself, though, is trusted code in the principal's browser.
- **The pairing key protects the relay, not the machine.** Any process running as the same OS user can read the user's keychain entry or call the loopback relay with it.
- **Same-user code beats any in-session check.** The shell proof stops a local process from widening the tier over HTTP through the loopback bootstrap. But arbitrary code running as the same OS user could still read the backend's memory, or send synthetic input to the confirmation dialog (UIPI only protects higher-integrity windows). Jailed agent runs (AppContainer: loopback and other processes blocked) cannot do either.
- **Approvals share the bootstrap exposure.** Exact approvals of "ask" actions (`POST /workflow-runs/{run_id}/escalations/{id}/approve`) are not shell-confirmed yet. A local un-jailed process could approve its own user-browser asks the same way (follow-up).
- **Open tier.** No prompts for sends, deletes and other actions. A prompt injection on any page can then make the agent act in signed-in sessions. Payments, purchases and account-security changes still ask, in Open as in every tier (principal decision 2026-10-04). The gateway derives the fact (`protected_action`) from the perceived control, and the policy treats a missing fact as protected. Like R3, the classification relies on labels: a payment button labelled only "Continue" in a form without a card field would not be caught. Submitting any form that holds a card or secret field counts as a payment, so sign-in forms also ask in Open. Open shows as `degraded` on the Posture page with the consent record.
- **Label-based classification.** As for the agent browser, a page that labels "Delete account" as "OK" gets R2 (the default), not R3. In Trusted, that action would not ask. The R4 secret-field checks do not depend on labels alone.
- **Synthetic events.** The extension clicks with `element.click()` and fills by setting the value and dispatching `input` / `change`. Some sites ignore untrusted events. Pressing Enter in a form uses `form.requestSubmit()`.
- **Navigation is a data channel.** A URL the agent navigates to can carry data out (`?q=<data>`). Below Open, navigation to a site outside the tier's lists asks.

### What has been verified

| Claim | Evidence |
|---|---|
| Tier x action matrix, floor (panic, unpaired, secret fields, R4, scheme, shared tabs, consent) in Rego | `policies/tests/user_browser_test.rego` (real OPA), parity cases in `tests/policy/test_policy_parity.py` |
| The same matrix through the real gateway + OPA, tainted asks never granted, Open-tier consent reason, tier read from process state | `tests/policy/test_user_browser_opa.py` |
| **Real extension in Playwright Chromium on a temporary profile** (no registry, no real profile): pinned ID, Strict shared-tab reads, asks and exact approvals, Assisted, Trusted navigate / click / type / scroll, Open R3 without asking, masked screenshot, the extension refusing secret typing, stale elements and pre-panic commands with the gateway bypassed | `tests/policy/test_browser_driver_contract.py` (headless `channel="chromium"`) |
| Port contract for both drivers (gateway first, panic under 100 ms and latched, secrets, observe mode) | `tests/policy/test_browser_driver_contract.py` |
| Pairing and relay (unpaired, mismatched, unpinned, bad session, unpair revokes, loopback-only, browser headers refused), tier principal-only, consent recorded, posture | `apps/backend/tests/test_user_browser_endpoint.py`, `tests/unit/test_user_browser.py` |
| Native host framing, origin pinning, loopback-only backend URL, refusal codes; registration with a fake registry and temp dirs | `tests/unit/test_user_browser.py` |
| Desktop shell proof: valid, wrong key, other request, expired, replayed and missing proofs; site lists required; narrowing without a proof; widening without one refused and audited; pairing proof; no shell secret refuses widening; non-desktop rule unchanged | `tests/unit/test_shell_confirmation.py`, `apps/backend/tests/test_user_browser_endpoint.py` |
| The secret is handed over on stdin. A real child receives it, then spawns a grandchild: the grandchild's stdin is not the pipe, and the secret is in no env, argv, stdin or log line (a negative control with stdin left attached fails this check) | `tests/unit/test_shell_confirmation.py` |
| Rust wiring as strings: dialog plugin, commands, stdin hand-off, risk texts and message formats identical to Python, no `dialog:*` permission for the webview | `tests/backend/test_desktop_packaging.py` |

**Not verified:** behaviour against a real signed-in profile in installed Chrome, Edge or Firefox; the native host launched by a real browser through a real registry entry or manifest; Firefox at all (the extension code is written for it but was only run in Chromium); AMO signing; macOS and Linux host paths on real machines; the frozen `locus-backend` binary acting as the host; **the Rust shell changes for the confirmation dialog (not compiled locally; CI compiles them) and the dialog on a real desktop**. No UI calls `confirm_browser_tier` yet.

## Verify on macOS

macOS is **unverified**: none of this has run on a real Mac. Before claiming macOS support, check each item below on a signed build (`hardenedRuntime: true`):

1. **Accessibility permission (required for desktop tools).** The first `desktop_observe` makes `AXIsProcessTrusted()` return false and the tool reports "this process lacks the macOS Accessibility permission". Grant Lattix Locus in System Settings > Privacy & Security > Accessibility, then relaunch. Check which entry TCC lists. The AX calls run in the `locus-backend` sidecar, which TCC normally attributes to the parent `Lattix Locus.app` (the responsible process). If it lists `locus-backend` separately, the sidecar's signature and identifier must stay stable across updates or the grant is lost on every update.
2. **Screen Recording permission.** v1 does not capture the screen. Desktop tools read the AX tree, and `browser_screenshot` captures only the agent's own Chromium page. So **no Screen Recording prompt should appear**. If one does, record what triggered it. Desktop frame capture (follow-up) will need this permission, and its prompt and denial path must be tested then.
3. **Panic hotkey.** Cmd+Alt+Shift+Esc must fire with the app in the background, with the window hidden to the tray, and while the webview is busy. Carbon hotkeys need no permission. Confirm that no Input Monitoring prompt appears. Confirm that `POST /computer-use/panic` lands (`computer_use.panic` in the audit log) and that an in-flight `desktop_type` into TextEdit stops.
4. **Takeover indicator.** Set the mode to `takeover`. Within about 1 s the menu bar shows "AGENT IN CONTROL" next to the tray icon, and the tooltip changes. Both clear after reset.
5. **Playwright Chromium.** On first run `<app_home>/playwright` gets Chromium and the headless shell. Check that the bundled `node` driver was executable after PyInstaller unpacking (first run sets the exec bit if it is missing). Check that Gatekeeper and quarantine do not block the downloaded Chromium. Check that the agent browser launches from the frozen backend.
6. **AX behaviour.** Secure text fields (`AXSecureTextField`) are never read or typed. The frontmost-app check refuses to send keys to another app. Walk caps hold on a large window (Xcode, Safari).
7. **Scenario suite.** Run `tests/policy/test_computer_use_opa.py` on the Mac (real OPA, real Chromium). Then run the doc 12 section 9 scenarios.

## Dependencies (P28 / P29 / P30)

| Package | Version | Licence | Provenance and why |
|---|---|---|---|
| `playwright` | 1.63.0 | Apache-2.0 | Microsoft. The maintained FOSS Chromium driver with accessibility-aware locators. Transitive dependencies: `greenlet` (MIT), `pyee` (MIT). Chromium comes from Microsoft's Playwright CDN (`python -m playwright install chromium`). |
| `comtypes` | 1.4.17 (Windows) | MIT | The community `enthought/comtypes` project. Calls `UIAutomationCore` directly. `uiautomation` and `pywinauto` were rejected under P28 (maintainer provenance). |
| `pyobjc-framework-ApplicationServices` | 12.2.2 (macOS) | MIT | PyObjC (Ronald Oussoren). The standard Python bridge to the AX API. |
| `tldextract` | >=5.1 (5.3.1 tested) | BSD-3-Clause | John Kurkowski (US). Public Suffix List for user-browser sites (LOCUS-350). It was already shipped as a dependency of `presidio-analyzer` and is now declared directly. It uses only the bundled PSL snapshot, never a network fetch. |

The egress proxy is our own code, about 150 lines. mitmproxy (MIT) was considered, but full TLS interception and a CA are far more than a host allowlist needs.

## Follow-ups

- Signed native helper (12 §4): OS-level preemption on physical input, HUD and screen border, panic outside the Python process.
- Encrypted frame storage. Sensitive-screen detection that drops whole frames rather than masking fields.
- Per-app R3 standing-grant override of the built-in deny list (12 §6). v1 does not allow overrides at all.
- Run the scenario suite on a real Mac before claiming macOS (12 §9).
- Build and smoke-test the desktop bundle with the hotkey, tray indicator and bundled Playwright driver on Windows and macOS. The Rust shell changes were not compiled in LOCUS-346.
- A web UI control for the computer-use mode and takeover state (P31).
