# 12 · Computer Use

**Decision (D-06):** full desktop control on Windows and macOS is an H1 commitment, alongside browser control. This is the highest-risk capability in the product, so this document is mostly about containment.

## 1. Modes

| Mode | What the agent can do | Default for |
|---|---|---|
| **Observe** | Read screen and accessibility tree; no input | Context gathering ("what's on my screen?") |
| **Assist** | Highlight targets and propose actions; the user clicks | Learning a new app; high-risk screens |
| **Takeover** | Drive input in the user's real session | Delegated runs that need desktop apps |
| **Isolated** (H2) | Drive a separate desktop session or VM | Unattended runs, untrusted sites, risky apps |

A run requests a mode as a capability. Takeover and Isolated are R2 capabilities (in the envelope) and every individual action is still classified R0–R4 by what it does.

## 2. Perception

Order of preference, cheapest and most reliable first:

1. **Accessibility tree:** Windows UI Automation; macOS Accessibility (AX) API. Gives roles, names, values, states and bounds without pixels.
2. **App-native APIs** where a tool exists (Graph for Outlook data, CDP for browsers, Office automation where safe). Prefer the tool over the pixels.
3. **Screenshots + vision model** when the tree is missing or unhelpful (canvas apps, remote desktops, games). Captured with ScreenCaptureKit on macOS, Windows Graphics Capture on Windows.
4. **OCR** as a fallback for text extraction from frames.

All perception output is **untrusted** (screens can contain injected instructions). Laya screens extracted text with the injection noul before it enters the engine context.

## 3. Action

1. **Semantic actions first:** UIA patterns (Invoke, Value, Toggle, Selection) and AX actions (`AXPress`, set value).
2. **Synthetic input** as fallback: Windows `SendInput`; macOS `CGEvent`. Coordinates come from current perception, never from stale frames.
3. **Verification after each action:** re-perceive and check the expected change; mismatch → retry once, then revise or ask.

## 4. Native helper

Computer use runs in a separate, signed **native helper** process, not in the Python service:

- Holds the OS permissions (macOS Accessibility and Screen Recording TCC grants; Windows UIAccess where needed), so the main service doesn't.
- Exposes a narrow local IPC interface to the gateway only (authenticated with a per-boot key).
- Implements the panic key, input preemption and HUD at the OS level, so they work when the rest of the app is busy or hung.
- Enforces the app allow/deny list itself (defense in depth with policy).

## 5. Human control

| Control | Behavior |
|---|---|
| **Visible state** | HUD plus a patterned screen border whenever the agent controls input |
| **Preemption** | Any physical mouse movement or keypress pauses the agent within 100 ms and yields input |
| **Resume / take over** | HUD buttons; taking over ends the screen session and lets the run continue in background or block |
| **Panic key** | Global hotkey: stop all runs, release input, close agent browser profiles. Configurable; tested at first run |
| **Lock and sleep** | Agent never acts on the lock screen or login window; screen runs pause on lock and resume only on explicit request |

## 6. Safety boundaries

- **Denied by default:** password managers, banking and payment apps, system settings and security tools, the OS credential prompts (UAC, macOS authentication dialogs), Locus's own Security space. Overridable per app with an R3 standing grant, except credential prompts (R4).
- **Sensitive-screen detection:** Laya nouls flag login, payment and secret-bearing screens; the agent stops and asks, and frames from these screens are not retained.
- **Typing secrets:** the agent never types credentials from memory or context. Where login is needed, it asks the user to log in, or uses a gateway secret-by-reference injection into a known field after an R3 approval.
- **Clipboard:** reading the clipboard is R0 but tainted; writing is R1; the agent restores the previous clipboard content after use.
- **Downloads and installs:** downloading executables or installing software is R3.
- **Frames:** recorded per run for audit with a retention policy (default 14 days), stored encrypted, redacting detected sensitive regions.

## 7. Browser control

- A **dedicated agent browser profile** (Chromium via CDP) is the default: separate cookies and storage, its own egress via the per-run proxy, and downloads into the run workspace.
- Using the user's own browser profile (logged-in sessions) is an H2 capability gated by an R3 grant per site.
- Page content is untrusted. Form submission is R2 or R3 depending on the target (payment and account-changing forms are R3).

## 8. Isolated mode (H2)

| Platform | Options |
|---|---|
| Windows | A separate local user session or Windows Sandbox/Hyper-V VM; the agent drives it, the user can view it |
| macOS | A separate user session or a VM via Virtualization.framework |

Isolated mode is preferred for unattended screen work, untrusted sites and apps with broad local access.

## 9. Platform parity

Windows and macOS are equal citizens for H1. A capability isn't "shipped" until it passes the same scenario suite on both (Linux desktop is best-effort).

## 10. Evaluation

A computer-use scenario suite (office documents, web forms, desktop apps, multi-app flows) runs on both platforms in CI-adjacent VMs, measuring task success, actions per task, preemption latency and safety-boundary adherence. Safety scenarios (injected instructions on screen, payment forms, credential prompts) must have zero escapes.
