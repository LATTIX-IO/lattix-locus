"""Tests for WS7: Memory decay / relevance weighting."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

# We test the decay function directly from main
import sys

sys.path.insert(
    0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "apps" / "backend")
)

from app.main import _memory_age_decay_factor, _rank_hybrid_memory_entries


class TestMemoryAgeDecayFactor:
    def test_recent_entry_near_one(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        entry = {"at": now_iso}
        factor = _memory_age_decay_factor(entry, half_life_days=30.0)
        assert 0.95 <= factor <= 1.0

    def test_old_entry_decays(self):
        old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        entry = {"at": old_time}
        factor = _memory_age_decay_factor(entry, half_life_days=30.0)
        assert 0.2 <= factor <= 0.3  # ~0.25 after 2 half-lives

    def test_half_life_precision(self):
        half_life = 30.0
        half_life_time = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        entry = {"at": half_life_time}
        factor = _memory_age_decay_factor(entry, half_life_days=half_life)
        assert 0.45 <= factor <= 0.55  # should be ~0.5

    def test_zero_half_life_returns_one(self):
        entry = {"at": "2020-01-01T00:00:00+00:00"}
        assert _memory_age_decay_factor(entry, half_life_days=0) == 1.0

    def test_missing_timestamp_returns_one(self):
        assert _memory_age_decay_factor({}, half_life_days=30.0) == 1.0

    def test_invalid_timestamp_returns_one(self):
        entry = {"at": "not-a-date"}
        assert _memory_age_decay_factor(entry, half_life_days=30.0) == 1.0

    def test_created_at_fallback(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        entry = {"created_at": now_iso}
        factor = _memory_age_decay_factor(entry, half_life_days=30.0)
        assert factor > 0.9


class TestRankWithDecay:
    def _make_entry(self, content, tier, days_old=0):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        return {"content": content, "tier": tier, "at": ts}

    @patch.dict(
        os.environ,
        {"LOCUS_MEMORY_DECAY_ENABLED": "true", "LOCUS_MEMORY_DECAY_HALF_LIFE_DAYS": "30"},
    )
    def test_decay_reduces_old_long_term_score(self):
        recent = self._make_entry("keyword match", "long-term", days_old=1)
        old = self._make_entry("keyword match", "long-term", days_old=90)
        ranked = _rank_hybrid_memory_entries(
            [recent, old],
            query_text="keyword",
            runtime_role="",
        )
        assert ranked[0]["retrieval_score"] > ranked[1]["retrieval_score"]

    @patch.dict(
        os.environ,
        {"LOCUS_MEMORY_DECAY_ENABLED": "true", "LOCUS_MEMORY_DECAY_HALF_LIFE_DAYS": "30"},
    )
    def test_short_term_not_decayed(self):
        old = self._make_entry("keyword match", "short-term", days_old=90)
        ranked = _rank_hybrid_memory_entries(
            [old],
            query_text="keyword",
            runtime_role="",
        )
        # Short-term base is 90 + overlap bonus, should not be decayed
        assert ranked[0]["retrieval_score"] >= 90

    @patch.dict(os.environ, {"LOCUS_MEMORY_DECAY_ENABLED": "false"})
    def test_decay_disabled_no_effect(self):
        old = self._make_entry("keyword match", "long-term", days_old=365)
        ranked = _rank_hybrid_memory_entries(
            [old],
            query_text="keyword",
            runtime_role="",
        )
        # Without decay, score should be base (70) + overlap bonus
        assert ranked[0]["retrieval_score"] >= 70
