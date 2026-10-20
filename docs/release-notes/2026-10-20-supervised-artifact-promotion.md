Release-Impact: minor
Version: 0.9

LOCUS-468/B adds a new opt-in, supervisor-owned artifact promotion ledger.
It binds one changed file to the independently verified StageLease manifest,
an authenticated one-use human-approved Gateway decision, a short expiration,
and a separately authorized file-write decision. Linux supervisor promotion
uses a single-file atomic rename with a private, durable rollback witness.
Interrupted promotion must be reconciled without replay. Any disagreement
in source or stage hashes, unsupported file type, unsafe path, or missing
approval fails closed.

No Docker child can mount or write original workspaces. This is deliberately
limited to one existing-parent regular-file add or content modification.
Deleting files, publishing multiple files as one atomic unit, unsupported
platforms and automatic approval remain disabled.
