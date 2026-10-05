"""Tests for locus_runtime.context_dedup — File-aware context dedup."""

from __future__ import annotations

from locus_runtime.context_dedup import _extract_file_path, dedup_file_operations


class TestExtractFilePath:
    def test_from_metadata(self):
        entry = {"metadata": {"file_path": "src/main.py"}, "content": "something"}
        assert _extract_file_path(entry) == "src/main.py"

    def test_from_content_pattern(self):
        entry = {"content": "I read `config.json` and processed it"}
        assert _extract_file_path(entry) == "config.json"

    def test_modified_pattern(self):
        entry = {"content": "modified src/app.ts to fix the bug"}
        assert _extract_file_path(entry) == "src/app.ts"

    def test_no_file(self):
        entry = {"content": "Hello world, no files here"}
        assert _extract_file_path(entry) is None

    def test_empty_entry(self):
        assert _extract_file_path({}) is None


class TestDedupFileOperations:
    def test_empty(self):
        assert dedup_file_operations([]) == []

    def test_no_file_entries_pass_through(self):
        entries = [
            {"content": "Hello world"},
            {"content": "Some analysis"},
        ]
        result = dedup_file_operations(entries)
        assert len(result) == 2

    def test_dedup_same_file(self):
        entries = [
            {"content": "read `app.py` version 1", "metadata": {"file_path": "app.py"}},
            {"content": "no file here"},
            {"content": "modified `app.py` version 2", "metadata": {"file_path": "app.py"}},
        ]
        result = dedup_file_operations(entries)
        assert len(result) == 2
        assert result[0]["content"] == "no file here"
        assert "version 2" in result[1]["content"]

    def test_different_files_kept(self):
        entries = [
            {"content": "read", "metadata": {"file_path": "a.py"}},
            {"content": "read", "metadata": {"file_path": "b.py"}},
        ]
        result = dedup_file_operations(entries)
        assert len(result) == 2

    def test_keeps_last_per_file(self):
        entries = [
            {"content": "v1", "metadata": {"file_path": "x.py"}},
            {"content": "v2", "metadata": {"file_path": "x.py"}},
            {"content": "v3", "metadata": {"file_path": "x.py"}},
        ]
        result = dedup_file_operations(entries)
        assert len(result) == 1
        assert result[0]["content"] == "v3"

    def test_mixed_file_and_nonfile(self):
        entries = [
            {"content": "v1", "metadata": {"file_path": "x.py"}},
            {"content": "analysis result"},
            {"content": "v2", "metadata": {"file_path": "x.py"}},
            {"content": "summary"},
        ]
        result = dedup_file_operations(entries)
        assert len(result) == 3
        file_entries = [e for e in result if e.get("metadata", {}).get("file_path") == "x.py"]
        assert len(file_entries) == 1
        assert file_entries[0]["content"] == "v2"
