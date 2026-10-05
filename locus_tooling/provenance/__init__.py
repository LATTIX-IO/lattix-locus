"""D-29 provenance inspection: attestations, inspection tooling and the dependency gate.

See docs/PROVENANCE.md. Modules:

* :mod:`.records` -- attestation paths, schema validation and the passing verdict
  (stdlib only; used by the runtime model gate and CI).
* :mod:`.inspection` -- runs an inspection and drafts an attestation.
* :mod:`.static_rules`, :mod:`.dynamic`, :mod:`.sbom`, :mod:`.vulns`,
  :mod:`.screening` -- the individual checks.
* :mod:`.models` -- the weights-only format check, the behavioural-eval hook and
  the runtime local-model verdict.
* :mod:`.gate` -- the CI dependency gate (no network).
"""
