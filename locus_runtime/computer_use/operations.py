"""Computer-use agent tools and the gateway operations they need (LOCUS-341, LOCUS-350).

Dependency-free so the run envelope can import it without the browser stack.
"""

from __future__ import annotations

from typing import Any

#: Agent tool → gateway operations it needs (agent_policy ``allowed_tools``).
#: Agent-browser tools also need ``network_egress``: the agent browser
#: authorizes every request host as a ``network_egress`` action. The
#: principal's own browser (``user_browser_*``) has its own network; its
#: navigation is governed by the browser tier instead (``user_browser`` policy).
COMPUTER_USE_TOOL_OPERATIONS: dict[str, tuple[str, ...]] = {
    "browser_navigate": ("browser_navigate", "network_egress"),
    "browser_read": ("browser_read", "network_egress"),
    "browser_act": ("browser_act", "network_egress"),
    "browser_screenshot": ("browser_read",),
    "user_browser_tabs": ("user_browser_read",),
    "user_browser_observe": ("user_browser_read",),
    "user_browser_navigate": ("user_browser_navigate",),
    "user_browser_act": ("user_browser_act",),
    "user_browser_screenshot": ("user_browser_read",),
    "desktop_observe": ("ui_observe",),
    "desktop_click": ("ui_click",),
    "desktop_type": ("ui_type",),
    "desktop_key": ("ui_key",),
}
BROWSER_TOOLS = frozenset(
    name for name in COMPUTER_USE_TOOL_OPERATIONS if name.startswith("browser_")
)
USER_BROWSER_TOOLS = frozenset(
    name for name in COMPUTER_USE_TOOL_OPERATIONS if name.startswith("user_browser_")
)
DESKTOP_TOOLS = frozenset(
    name for name in COMPUTER_USE_TOOL_OPERATIONS if name.startswith("desktop_")
)
COMPUTER_USE_TOOL_NAMES = BROWSER_TOOLS | USER_BROWSER_TOOLS | DESKTOP_TOOLS

#: Model-facing browser tool → (driver profile, port op). One table for both
#: drivers of the ``BrowserDriver`` port (browser_contract).
BROWSER_TOOL_PORT: dict[str, tuple[str, str]] = {
    "browser_navigate": ("agent", "navigate"),
    "browser_read": ("agent", "read"),
    "browser_act": ("agent", "act"),
    "browser_screenshot": ("agent", "screenshot"),
    "user_browser_tabs": ("user", "tabs"),
    "user_browser_observe": ("user", "read"),
    "user_browser_navigate": ("user", "navigate"),
    "user_browser_act": ("user", "act"),
    "user_browser_screenshot": ("user", "screenshot"),
}


def browser_profiles_for(tools: Any) -> frozenset[str]:
    """Driver profiles (``"agent"`` / ``"user"``) the listed tools need."""
    return frozenset(
        BROWSER_TOOL_PORT[str(name)][0] for name in tools or () if str(name) in BROWSER_TOOL_PORT
    )


def computer_use_operations(tools: Any) -> frozenset[str]:
    """Gateway operations for the computer-use tools among ``tools``."""
    out: set[str] = set()
    for name in tools or ():
        out.update(COMPUTER_USE_TOOL_OPERATIONS.get(str(name), ()))
    return frozenset(out)
