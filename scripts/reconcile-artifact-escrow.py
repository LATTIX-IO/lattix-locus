#!/usr/bin/env python3
"""LOCUS-469: fail-closed supervisor startup/maintenance escrow reconciliation.

Never dispatches a child, grants an approval or retries an artifact write.
Requires the existing private SQLite ledger from the supervisor installation.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.pod_artifact_escrow import DetachedArtifactEscrow
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_bindings import PodStageBindings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--batch", type=int, default=50)
    args = parser.parse_args()
    try:
        if sys.platform != "linux" or os.geteuid() == 0:
            raise AdmissionDenied("escrow recovery requires nonroot Linux supervisor")
        path = Path(args.ledger).absolute()
        meta = path.lstat()
        parent = path.parent.lstat()
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != os.geteuid()
            or stat.S_IMODE(meta.st_mode) & 0o077
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise AdmissionDenied("ledger path and parent must be owner-private")
        ledger = PodReservationLedger(path)
        stages = PodStageBindings(ledger, PodActionReceipts(ledger))
        results = DetachedArtifactEscrow(stages).reconcile_pending(limit=args.batch)
        print(json.dumps([
            {"escrow_sha256_prefix": escrow_id[:12], "outcome": outcome}
            for escrow_id, outcome in results
        ], sort_keys=True))
        return 2 if any(outcome == "held-for-review" for _, outcome in results) else 0
    except (AdmissionDenied, OSError, ValueError) as exc:
        print("escrow recovery blocked: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
