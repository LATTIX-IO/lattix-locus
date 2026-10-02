"""Make the apps/evals package importable in tests without installation."""

from __future__ import annotations

import sys
from pathlib import Path

_EVALS_SRC = Path(__file__).resolve().parents[2] / "apps" / "evals"
if str(_EVALS_SRC) not in sys.path:
    sys.path.insert(0, str(_EVALS_SRC))
