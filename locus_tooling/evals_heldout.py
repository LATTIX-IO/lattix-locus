"""Where the private RSI held-out split comes from (LOCUS-382).

The held-out tasks the self-improvement loop is promoted on live in a separate,
private repository, never in this one (the loop's coding agent works in a clone
of this repository). ``lattix evals sync`` (:mod:`locus_tooling.evals_sync`)
fetches the pinned ref below with the user's own git credentials and installs it
read-only under ``<app_home>/evals/heldout/<digest>/``.

This file is a D-22 protected path (gate definitions, merge-guard baseline,
CODEOWNERS): pointing the evaluator at another repository or an older tag would
change the exam, so only the principal changes it. Rotation (a new tag) is
documented in the private repository's README and in
``docs/development/rsi-scorecard.md``.
"""

from __future__ import annotations

#: The private repository holding ``heldout/*.yaml`` and ``MANIFEST.json``.
HELDOUT_REPOSITORY = "https://github.com/LATTIX-IO/locus-evals-private.git"
#: The pinned ref (a release tag; tags are never moved once synced).
HELDOUT_REF = "v1"

#: Overrides for a runner (the principal's environment, never the agent's).
REPOSITORY_ENV = "LOCUS_EVALS_REPO"
REF_ENV = "LOCUS_EVALS_REF"
#: ``1`` refuses a tag that is unsigned or whose signature could not be verified.
REQUIRE_SIGNED_ENV = "LOCUS_EVALS_REQUIRE_SIGNED"
#: A local private folder of held-out ``<task-id>.yaml`` files; overrides the sync.
HELDOUT_DIR_ENV = "LOCUS_EVAL_HELDOUT_DIR"
