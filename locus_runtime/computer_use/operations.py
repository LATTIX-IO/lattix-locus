"""Computer-use agent tools and the gateway operations they need (LOCUS-341).

Dependency-free so the run envelope can import it without the browser stack.
"""

from __future__ import annotations

from typing import Any

#: Agent tool → gateway operations it needs (agent_policy ``allowed_tools``).
#: Browser tools also need ``network_egress``: the agent browser authorizes
#: every request host as a ``network_egress`` action.
COMPUTER_USE_TOOL_OPERATIONS: dict[str, tuple[str, ...]] = {
    "browser_navigate": ("browser_navigate", "network_egress"),
    "browser_read": ("browser_read", "network_egress"),
    "browser_act": ("browser_act", "network_egress"),
    "browser_screenshot": ("browser_read",),
    "desktop_observe": ("ui_observe",),
    "desktop_click": ("ui_click",),
    "desktop_type": ("ui_type",),
    "desktop_key": ("ui_key",),
}
BROWSER_TOOLS = frozenset(
    name for name in COMPUTER_USE_TOOL_OPERATIONS if name.startswith("browser_")
)
DESKTOP_TOOLS = frozenset(
    name for name in COMPUTER_USE_TOOL_OPERATIONS if name.startswith("desktop_")
)
COMPUTER_USE_TOOL_NAMES = BROWSER_TOOLS | DESKTOP_TOOLS


def computer_use_operations(tools: Any) -> frozenset[str]:
    """Gateway operations for the computer-use tools among ``tools``."""
    out: set[str] = set()
    for name in tools or ():
        out.update(COMPUTER_USE_TOOL_OPERATIONS.get(str(name), ()))
    return frozenset(out)
