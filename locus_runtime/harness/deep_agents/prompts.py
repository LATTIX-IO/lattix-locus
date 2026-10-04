"""Trimmed prompts and tool descriptions for the Deep Agents harness (LOCUS-361 token diet).

Upstream's defaults are written for a general assistant: the ``write_todos``
description alone is ~1,000 tokens, the ``task`` description ~350, and the todo
system prompt tells the agent to finish with a prose answer (Locus finishes with
``submit`` and the verify gate). Every model turn resends them. These replace
them with what a Locus run needs; the envelope message already says to plan
first and to finish with ``submit``.
"""

from __future__ import annotations

#: Like the verified loop's ``update_plan``: the plan is recorded once and revised
#: when it changes. Upstream's prompt asks for a status update after every step,
#: which costs a whole model turn each time (measured: up to 6 per small task).
TODO_SYSTEM_PROMPT = (
    "Record your plan once with `write_todos`; call it again only when the plan itself "
    "changes, not to report progress. Finish by calling `submit`: it runs the verifiers."
)

TODO_TOOL_DESCRIPTION = (
    "Record the run plan: the full list of steps, each with a status (pending, "
    "in_progress or completed). A call replaces the previous list; call it again only "
    "when the plan changes."
)

#: ``{available_agents}`` is filled in by Deep Agents' SubAgentMiddleware.
TASK_TOOL_DESCRIPTION = (
    "Delegate a large, self-contained piece of the work to a sub-agent with the same "
    "tools and permissions. It returns one report and cannot submit. Put everything it "
    "needs in the description.\n\nAgents:\n{available_agents}"
)

#: Short parameter descriptions for the ``task`` schema the model sees.
TASK_PARAMETER_DESCRIPTIONS = {
    "description": "The whole sub-task, with the context it needs and what to report.",
    "subagent_type": "Name of the agent to use.",
}

SUBAGENT_DESCRIPTION = "Investigates or edits with the same tools, then reports."

SUBAGENT_PROMPT = (
    "You are a sub-agent working in the same repository and run as the main agent. Use "
    "the tools to do the delegated work, then reply with a short factual report of what "
    "you found or changed. You cannot submit the run."
)
