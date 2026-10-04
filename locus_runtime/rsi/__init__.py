"""Recursive self-improvement (RSI) scorecard: what "better" means for the loop (LOCUS-351).

* :mod:`.scorecard` -- the versioned :class:`~.scorecard.Scorecard` (a vector, not
  a single number) and the pure promotion rule :func:`~.scorecard.compare`.
* :mod:`.variants` -- the variant archive under ``LOCUS_LOOP_HOME/variants/``.
* :mod:`.candidate` -- :class:`~.candidate.CandidateInstance`, which runs the eval
  against candidate code in a separate, secret-free, OS-jailed instance.
* :mod:`.metering` -- the trusted model-endpoint proxy (egress point, token meter,
  canary watch) the candidate instance talks to.
* :mod:`.jail` / :mod:`.win_appcontainer` -- the candidate's OS jail (LOCUS-379):
  AppContainer on Windows, seatbelt on macOS, bubblewrap on Linux; no network.
* :mod:`.bridge` -- the stdio bridge the jailed candidate reaches its trusted
  parent through (model calls, policy decisions, agent commands, host git).

This package is a D-22 protected path (``locus_runtime/gate_definitions.py``,
``.github/CODEOWNERS``): the loop cannot redefine "better" or weaken the
candidate's isolation in a PR that auto-merges. Design and limits:
``docs/development/rsi-scorecard.md``.
"""

from __future__ import annotations
