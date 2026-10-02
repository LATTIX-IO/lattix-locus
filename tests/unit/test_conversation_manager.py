"""Tests for locus_runtime.conversation — ConversationManager."""

from __future__ import annotations

from locus_runtime.conversation import ConversationManager, _estimate_tokens


class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 0

    def test_short_text(self):
        result = _estimate_tokens("hello world")
        assert result >= 1

    def test_proportional(self):
        short = _estimate_tokens("one two three")
        long = _estimate_tokens("one two three four five six seven eight nine ten")
        assert long > short


class TestConversationManager:
    def _make_manager(self, **kwargs):
        return ConversationManager(
            session_id="test-session",
            run_id="test-run",
            **kwargs,
        )

    def test_add_turn(self):
        mgr = self._make_manager()
        turn = mgr.add_turn("user", "hello world")
        assert turn.role == "user"
        assert turn.content == "hello world"
        assert turn.turn_index == 0
        assert turn.token_estimate >= 1
        assert not turn.compacted

    def test_multiple_turns(self):
        mgr = self._make_manager()
        mgr.add_turn("system", "You are helpful.")
        mgr.add_turn("user", "What is 2+2?")
        mgr.add_turn("assistant", "2+2 equals 4.")
        assert len(mgr.turns) == 3
        assert mgr.turns[0].turn_index == 0
        assert mgr.turns[2].turn_index == 2

    def test_get_messages_format(self):
        mgr = self._make_manager()
        mgr.add_turn("system", "You are helpful.")
        mgr.add_turn("user", "Hi")
        mgr.add_turn("assistant", "Hello!")
        messages = mgr.get_messages()
        assert len(messages) == 3
        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1] == {"role": "user", "content": "Hi"}
        assert messages[2] == {"role": "assistant", "content": "Hello!"}

    def test_compaction_triggers_on_threshold(self):
        mgr = self._make_manager(max_tokens=100, compaction_threshold=0.5)
        for i in range(20):
            mgr.add_turn("user", f"Message {i} " * 10)
        # After many turns exceeding budget, some should be compacted
        compacted_count = sum(1 for t in mgr.turns if t.compacted)
        assert compacted_count > 0

    def test_compaction_preserves_recent_turns(self):
        mgr = self._make_manager(max_tokens=200, compaction_threshold=0.5)
        for i in range(15):
            mgr.add_turn("user" if i % 2 == 0 else "assistant", f"Turn {i} content " * 5)
        messages = mgr.get_messages()
        # Should still have recent messages
        assert len(messages) >= 1

    def test_get_last_reasoning_none(self):
        mgr = self._make_manager()
        mgr.add_turn("user", "Hello")
        assert mgr.get_last_reasoning() is None

    def test_get_last_reasoning(self):
        mgr = self._make_manager()
        mgr.add_turn(
            "assistant", "Answer", metadata={"reasoning_summaries": ["Thought A", "Thought B"]}
        )
        assert mgr.get_last_reasoning() == "Thought B"

    def test_serialize_deserialize(self):
        mgr = self._make_manager()
        mgr.add_turn("system", "sys prompt")
        mgr.add_turn("user", "question")
        mgr.add_turn("assistant", "answer", metadata={"reasoning_summaries": ["reason"]})
        serialized = mgr.serialize()
        restored = ConversationManager.deserialize(serialized)
        assert restored.session_id == mgr.session_id
        assert restored.run_id == mgr.run_id
        assert len(restored.turns) == len(mgr.turns)
        assert restored.turns[2].metadata["reasoning_summaries"] == ["reason"]

    def test_serialize_roundtrip_preserves_summary(self):
        mgr = self._make_manager(max_tokens=100, compaction_threshold=0.3)
        for i in range(10):
            mgr.add_turn("user", f"Long message {i} " * 15)
        serialized = mgr.serialize()
        restored = ConversationManager.deserialize(serialized)
        assert restored._summary == mgr._summary

    def test_total_tokens(self):
        mgr = self._make_manager()
        mgr.add_turn("user", "hello")
        assert mgr.total_tokens > 0

    def test_compact_with_few_turns_is_noop(self):
        mgr = self._make_manager()
        mgr.add_turn("user", "a")
        mgr.add_turn("assistant", "b")
        mgr.compact()
        assert all(not t.compacted for t in mgr.turns)
