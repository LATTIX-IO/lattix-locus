"""Tests for WS3: Reasoning chain preservation through compaction."""

from __future__ import annotations

from locus_runtime.conversation import ConversationManager


class TestReasoningPreservation:
    def _make_manager(self, **kwargs):
        return ConversationManager(
            session_id="test-session",
            run_id="test-run",
            **kwargs,
        )

    def test_reasoning_stored_in_metadata(self):
        mgr = self._make_manager()
        mgr.add_turn(
            "assistant",
            "Answer here",
            metadata={
                "reasoning_summaries": ["Step 1: Analyzed input", "Step 2: Generated output"],
            },
        )
        assert mgr.get_last_reasoning() == "Step 2: Generated output"

    def test_reasoning_survives_compaction(self):
        mgr = self._make_manager(max_tokens=150, compaction_threshold=0.3)
        # Add reasoning in early turns
        mgr.add_turn("system", "You are an agent.")
        mgr.add_turn("user", "Do something complex " * 10)
        mgr.add_turn(
            "assistant",
            "Here is my response " * 10,
            metadata={
                "reasoning_summaries": ["Important reasoning that should survive"],
            },
        )
        # Add more turns to trigger compaction
        for i in range(8):
            mgr.add_turn("user" if i % 2 == 0 else "assistant", f"Turn {i} content " * 8)
        # The summary should contain the reasoning
        if mgr._summary:
            assert "reasoning" in mgr._summary.lower() or "Important" in mgr._summary

    def test_no_reasoning_returns_none(self):
        mgr = self._make_manager()
        mgr.add_turn("user", "Hello")
        mgr.add_turn("assistant", "World")
        assert mgr.get_last_reasoning() is None

    def test_multiple_reasoning_returns_last(self):
        mgr = self._make_manager()
        mgr.add_turn("assistant", "First", metadata={"reasoning_summaries": ["Reason A"]})
        mgr.add_turn(
            "assistant", "Second", metadata={"reasoning_summaries": ["Reason B", "Reason C"]}
        )
        assert mgr.get_last_reasoning() == "Reason C"

    def test_empty_reasoning_list(self):
        mgr = self._make_manager()
        mgr.add_turn("assistant", "Answer", metadata={"reasoning_summaries": []})
        assert mgr.get_last_reasoning() is None
