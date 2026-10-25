Release-Impact: minor
Version: 0.11

Add a versioned, non-executable TaskPlan review projection that binds untrusted
plan proposals to server-authored actor, tenant, project, policy epoch,
classification, catalog and bounded budgets. The projection remains structural
and does **not** grant execution or replace operator approval. Production plan
review APIs, expiring receipts, and revision-pinned launch will be introduced
in subsequent gated increments under LOCUS-459. No user-data migration or
re-consent is required for this additive module.