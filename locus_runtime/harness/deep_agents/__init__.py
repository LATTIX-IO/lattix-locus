"""Locus extensions to LangChain Deep Agents (LOCUS-361, D-27, fork policy LOCUS-363).

Upstream ``deepagents`` stays a pinned, unmodified dependency. Everything that
makes it a Locus harness lives here and plugs into Deep Agents through its own
extension points (middleware, a harness profile, tools, the chat-model class,
the checkpointer):

* :mod:`.library` -- the only place the third-party stack is imported; checks
  the installed versions against the audited pins and forces LangSmith off.
* :mod:`.middleware` -- the verified loop's guarantees as agent middleware over
  :class:`~locus_runtime.harness.runtime_controller.RunController` (envelope
  guards, gateway mediation, plan recording, asks -> ``interrupt``, the verify
  gate through ``submit``, text-turn nudges, context compaction).
* :mod:`.compaction` -- the context compaction policy (pure, no third-party code).
* :mod:`.prompts` -- the trimmed prompts and tool descriptions (token diet).
* :mod:`.runtime` -- :class:`~.runtime.DeepAgentsRuntime`, the ``deep-agents``
  implementation of the ``AgentRuntime`` port, with the durable SQLite
  checkpointer and resume.

What upstream extension points cannot do is listed, with the decision taken, in
``docs/development/deep-agents-harness.md`` (patch ledger). Nothing is forked.

Importing this package does not import the third-party stack; only
:mod:`.runtime` / :mod:`.library` / :mod:`.middleware` do, and only
``create_runtime("deep-agents")`` imports those.
"""
