"""Tests for WS8: Embedding-aware duplicate detection."""

from __future__ import annotations

import os
from unittest.mock import patch

import sys

sys.path.insert(
    0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "apps" / "backend")
)

from app.main import _find_duplicate_memory_consolidation


class TestVectorDedup:
    @patch.dict(
        os.environ,
        {
            "LOCUS_MEMORY_VECTOR_DEDUP_ENABLED": "true",
            "LOCUS_MEMORY_VECTOR_DEDUP_THRESHOLD": "0.92",
        },
    )
    def test_vector_dedup_finds_similar(self):
        similar_entry = {
            "id": "existing-1",
            "content": "Summary of memory consolidation",
            "kind": "memory-consolidation",
            "metadata": {"kind": "memory-consolidation"},
            "similarity": 0.95,
        }
        with patch("app.main._POSTGRES_MEMORY") as mock_pg:
            mock_pg.enabled = True
            mock_pg.vector_enabled = True
            mock_pg.find_similar_entries.return_value = [similar_entry]
            result = _find_duplicate_memory_consolidation(
                bucket_id="test-bucket",
                memory_scope="session",
                consolidated_content="Summary of memory consolidation results",
            )
            assert result is not None
            assert result["id"] == "existing-1"
            mock_pg.find_similar_entries.assert_called_once()

    @patch.dict(os.environ, {"LOCUS_MEMORY_VECTOR_DEDUP_ENABLED": "true"})
    def test_vector_dedup_falls_back_to_overlap(self):
        with patch("app.main._POSTGRES_MEMORY") as mock_pg:
            mock_pg.enabled = True
            mock_pg.vector_enabled = True
            mock_pg.find_similar_entries.return_value = []  # No vector matches
            mock_pg.get_entries.return_value = [
                {
                    "kind": "memory-consolidation",
                    "content": "Exact same content for overlap",
                }
            ]
            mock_pg.healthcheck.return_value = True
            _find_duplicate_memory_consolidation(
                bucket_id="test-bucket",
                memory_scope="session",
                consolidated_content="Exact same content for overlap",
            )
            # Should fall through to overlap check
            mock_pg.get_entries.assert_called_once()

    @patch.dict(os.environ, {"LOCUS_MEMORY_VECTOR_DEDUP_ENABLED": "false"})
    def test_vector_dedup_disabled_uses_overlap_only(self):
        with patch("app.main._POSTGRES_MEMORY") as mock_pg:
            mock_pg.enabled = True
            mock_pg.vector_enabled = True
            mock_pg.healthcheck.return_value = True
            mock_pg.get_entries.return_value = []
            result = _find_duplicate_memory_consolidation(
                bucket_id="test-bucket",
                memory_scope="session",
                consolidated_content="Some content",
            )
            assert result is None
            # find_similar_entries should not be called when disabled
            mock_pg.find_similar_entries.assert_not_called()

    def test_empty_content_returns_none(self):
        result = _find_duplicate_memory_consolidation(
            bucket_id="test-bucket",
            memory_scope="session",
            consolidated_content="",
        )
        assert result is None
