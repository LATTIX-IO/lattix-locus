#!/usr/bin/python3
"""Fixed stage-only single-file writer for the trusted Locus supervisor.

Not a general-purpose Python/shell tool. Input comes only from a supervisor
authenticated StageDockerBroker stdin pipe. Exactly one output at a fixed
stage path is created, with no traversal, overwrite, network or extra tools.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    data = sys.stdin.buffer.read(32769)
    if not 0 < len(data) <= 32768:
        return 41
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open("/workspace/draft.txt", flags, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                return 43
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
