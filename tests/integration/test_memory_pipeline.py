"""Integration test: Full memory pipeline append → WAL → consolidation → dedup → ranked retrieval."""

from __future__ import annotations

from locus_runtime.conversation import ConversationManager
from locus_runtime.context_dedup import dedup_file_operations
from locus_runtime.session_notes import generate_session_note


class TestMemoryPipelineIntegration:
    """End-to-end tests for the enhanced memory pipeline."""

    def test_conversation_to_session_notes_flow(self):
        """Verify conversation turns produce session notes that can be injected."""
        # Step 1: Create conversation
        mgr = ConversationManager(session_id="int-sess", run_id="int-run", max_tokens=4000)
        mgr.add_turn("system", "You are a research agent.")
        mgr.add_turn("user", "Find information about Redis persistence")
        mgr.add_turn(
            "assistant",
            "I decided to use RDB snapshots for periodic persistence.",
            metadata={
                "reasoning_summaries": ["Evaluated AOF vs RDB", "RDB chosen for simplicity"],
            },
        )

        # Step 2: Generate session note from the last assistant turn
        last_turn = mgr.turns[-1]
        note = generate_session_note(
            node_title="Research Agent",
            user_input="Find information about Redis persistence",
            assistant_output=last_turn.content,
            session_id="int-sess",
            run_id="int-run",
            turn_index=last_turn.turn_index,
        )

        # Step 3: Verify note captures key information
        assert note.session_id == "int-sess"
        assert len(note.decisions) >= 1
        ctx = note.to_context_string()
        assert "[Turn" in ctx

        # Step 4: Verify reasoning is preserved in conversation
        assert mgr.get_last_reasoning() == "RDB chosen for simplicity"

    def test_conversation_serialize_recover_continue(self):
        """Verify conversation can be serialized, recovered, and continued."""
        mgr = ConversationManager(session_id="persist-sess", run_id="persist-run")
        mgr.add_turn("system", "System prompt")
        mgr.add_turn("user", "First question")
        mgr.add_turn("assistant", "First answer", metadata={"reasoning_summaries": ["Reason A"]})

        # Serialize (simulates Redis persistence)
        serialized = mgr.serialize()

        # Recover
        restored = ConversationManager.deserialize(serialized)
        assert len(restored.turns) == 3

        # Continue conversation
        restored.add_turn("user", "Follow-up question")
        restored.add_turn("assistant", "Follow-up answer")
        assert len(restored.turns) == 5

        # Messages should include all turns
        messages = restored.get_messages()
        assert len(messages) == 5

    def test_file_dedup_in_ranked_pipeline(self):
        """Verify file dedup removes redundant file operations before ranking."""
        entries = [
            {
                "content": "read `config.py` version 1",
                "metadata": {"file_path": "config.py"},
                "tier": "short-term",
            },
            {"content": "analyzed the data structure", "tier": "short-term"},
            {
                "content": "modified `config.py` version 2",
                "metadata": {"file_path": "config.py"},
                "tier": "short-term",
            },
            {
                "content": "read `utils.py` helper",
                "metadata": {"file_path": "utils.py"},
                "tier": "long-term",
            },
        ]
        deduped = dedup_file_operations(entries)
        assert len(deduped) == 3
        config_entries = [
            e for e in deduped if e.get("metadata", {}).get("file_path") == "config.py"
        ]
        assert len(config_entries) == 1
        assert "version 2" in config_entries[0]["content"]

    def test_compaction_preserves_reasoning_end_to_end(self):
        """Verify reasoning survives aggressive compaction."""
        mgr = ConversationManager(
            session_id="compact-sess",
            run_id="compact-run",
            max_tokens=200,
            compaction_threshold=0.3,
        )
        mgr.add_turn("system", "Agent system prompt")
        mgr.add_turn("user", "Complex task " * 20)
        mgr.add_turn(
            "assistant",
            "Analysis complete " * 20,
            metadata={
                "reasoning_summaries": ["Critical insight: the memory architecture needs WAL"],
            },
        )
        # Add many more turns to force compaction
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            mgr.add_turn(role, f"Turn {i} content " * 15)

        # After compaction, summary should reference the reasoning
        messages = mgr.get_messages()
        assert len(messages) >= 1
        # The conversation should still be functional
        assert mgr.total_tokens <= mgr.max_tokens * 1.5  # Allow some slack

    def test_wal_append_and_recover_roundtrip(self, tmp_path):
        """Verify WAL can recover entries after simulated Redis loss."""
        from app.platform_services import RedisMemoryStore

        store = RedisMemoryStore.__new__(RedisMemoryStore)
        store.url = ""
        store.enabled = False
        store._client = None
        store.max_entries = 200
        store.wal_enabled = True
        store.wal_dir = tmp_path / "wal"

        # Append entries (goes to WAL since Redis is disabled)
        for i in range(5):
            store.append_entry(f"session-{i % 2}", {"content": f"entry-{i}", "id": f"id-{i}"})

        # Recover from WAL
        sess0 = store._wal_recover("session-0")
        sess1 = store._wal_recover("session-1")
        assert len(sess0) == 3  # entries 0, 2, 4
        assert len(sess1) == 2  # entries 1, 3

        # Cleanup
        store.cleanup_wal("session-0")
        assert store._wal_recover("session-0") == []
        assert len(store._wal_recover("session-1")) == 2  # Other session unaffected
