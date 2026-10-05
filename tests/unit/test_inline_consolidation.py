"""Tests for WS4: Inline consolidation triggers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import sys

sys.path.insert(
    0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "apps" / "backend")
)

from app.main import _maybe_trigger_inline_consolidation


class TestInlineConsolidation:
    @patch.dict(
        os.environ,
        {
            "LOCUS_MEMORY_INLINE_CONSOLIDATION_ENABLED": "true",
            "LOCUS_MEMORY_INLINE_CONSOLIDATION_THRESHOLD": "5",
        },
    )
    def test_triggers_when_threshold_exceeded(self):
        with (
            patch("app.main._POSTGRES_MEMORY") as mock_pg,
            patch("app.main._run_memory_consolidation"),
            patch("threading.Thread") as mock_thread,
        ):
            mock_pg.enabled = True
            mock_pg.healthcheck.return_value = True
            mock_pg.count_consolidation_candidates.return_value = 7
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            _maybe_trigger_inline_consolidation("bucket-1", memory_scope="session")

            mock_pg.count_consolidation_candidates.assert_called_once_with(status="pending")
            mock_thread.assert_called_once()
            mock_thread_instance.start.assert_called_once()

    @patch.dict(
        os.environ,
        {
            "LOCUS_MEMORY_INLINE_CONSOLIDATION_ENABLED": "true",
            "LOCUS_MEMORY_INLINE_CONSOLIDATION_THRESHOLD": "10",
        },
    )
    def test_no_trigger_below_threshold(self):
        with (
            patch("app.main._POSTGRES_MEMORY") as mock_pg,
            patch("threading.Thread") as mock_thread,
        ):
            mock_pg.enabled = True
            mock_pg.healthcheck.return_value = True
            mock_pg.count_consolidation_candidates.return_value = 3

            _maybe_trigger_inline_consolidation("bucket-1", memory_scope="session")

            mock_thread.assert_not_called()

    @patch.dict(os.environ, {"LOCUS_MEMORY_INLINE_CONSOLIDATION_ENABLED": "false"})
    def test_disabled_is_noop(self):
        with patch("app.main._POSTGRES_MEMORY") as mock_pg:
            _maybe_trigger_inline_consolidation("bucket-1")
            mock_pg.count_consolidation_candidates.assert_not_called()

    @patch.dict(os.environ, {"LOCUS_MEMORY_INLINE_CONSOLIDATION_ENABLED": "true"})
    def test_postgres_unavailable_is_noop(self):
        with patch("app.main._POSTGRES_MEMORY") as mock_pg:
            mock_pg.enabled = False
            _maybe_trigger_inline_consolidation("bucket-1")
            mock_pg.count_consolidation_candidates.assert_not_called()
