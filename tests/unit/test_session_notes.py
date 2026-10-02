"""Tests for locus_runtime.session_notes — Session Auto-Notes."""

from __future__ import annotations

from locus_runtime.session_notes import (
    SessionNote,
    _extract_decisions,
    _extract_files_modified,
    _extract_tools_used,
    _summarize_rule_based,
    generate_session_note,
)


class TestExtractDecisions:
    def test_decision_patterns(self):
        text = "I decided to use the REST API approach for this task."
        decisions = _extract_decisions(text)
        assert len(decisions) >= 1
        assert any("REST API" in d for d in decisions)

    def test_will_pattern(self):
        text = "We will refactor the authentication module next."
        decisions = _extract_decisions(text)
        assert len(decisions) >= 1

    def test_no_decisions(self):
        text = "Hello world."
        assert _extract_decisions(text) == []

    def test_max_decisions(self):
        text = (
            "I decided to use React. I selected TypeScript. "
            "I chose Tailwind. I opted for PostgreSQL. I'm going with Docker."
        )
        decisions = _extract_decisions(text)
        assert len(decisions) <= 3


class TestExtractFilesModified:
    def test_from_tool_calls(self):
        tool_calls = [
            {"name": "write_file", "arguments": {"file_path": "src/main.py"}},
            {"name": "edit_file", "arguments": {"path": "README.md"}},
        ]
        files = _extract_files_modified("", tool_calls)
        assert "src/main.py" in files
        assert "README.md" in files

    def test_from_text(self):
        text = "I modified `config.json` and `app.py` in the process."
        files = _extract_files_modified(text)
        assert "config.json" in files
        assert "app.py" in files

    def test_empty(self):
        assert _extract_files_modified("", None) == []


class TestExtractToolsUsed:
    def test_tool_names(self):
        tool_calls = [
            {"name": "read_file"},
            {"name": "write_file"},
            {"name": "read_file"},  # duplicate
        ]
        tools = _extract_tools_used(tool_calls)
        assert tools == ["read_file", "write_file"]

    def test_empty(self):
        assert _extract_tools_used(None) == []


class TestSummarizeRuleBased:
    def test_basic_summary(self):
        result = _summarize_rule_based(
            "Code Agent", "Fix the bug", "I fixed the null pointer exception"
        )
        assert "Code Agent" in result
        assert "Fix the bug" in result


class TestGenerateSessionNote:
    def test_generates_note(self):
        note = generate_session_note(
            node_title="Research Agent",
            user_input="Find information about memory systems",
            assistant_output="I decided to use Redis for short-term and Postgres for long-term storage.",
            tool_calls=[{"name": "search_web", "arguments": {"query": "memory systems"}}],
            session_id="sess-1",
            run_id="run-1",
            turn_index=0,
        )
        assert isinstance(note, SessionNote)
        assert note.session_id == "sess-1"
        assert note.turn_index == 0
        assert len(note.summary) > 0
        assert "search_web" in note.tools_used

    def test_to_context_string(self):
        note = generate_session_note(
            node_title="Agent",
            user_input="input",
            assistant_output="I decided to use approach A. Modified `file.py`.",
            session_id="s",
            run_id="r",
            turn_index=5,
        )
        ctx = note.to_context_string()
        assert "[Turn 5]" in ctx

    def test_to_dict(self):
        note = generate_session_note(
            node_title="Agent",
            user_input="hello",
            assistant_output="world",
        )
        d = note.to_dict()
        assert "summary" in d
        assert "decisions" in d
        assert "files_modified" in d
        assert "tools_used" in d
