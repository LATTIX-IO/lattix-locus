Release-Impact: minor
Version: 0.6

Add a trusted supervisor action write-ahead receipt ledger for Zero Trust-admitted ephemeral pods. Intents are authorized and persisted before external dispatch, and ambiguous outcomes are not automatically retried. Store only action fingerprints, audit references, and evidence digests. This does not enable general agent execution or claim exactly-once external effects.
