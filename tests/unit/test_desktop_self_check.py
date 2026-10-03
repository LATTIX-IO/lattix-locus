"""The frozen-bundle self-check imports every critical module (LOCUS-342)."""

from __future__ import annotations

import json

from locus_tooling import desktop_main


def test_self_check_passes_in_a_complete_environment(capsys) -> None:
    assert desktop_main.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert set(report["modules"]) == set(desktop_main._SELF_CHECK_MODULES)


def test_self_check_fails_when_a_module_is_missing(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_main, "_SELF_CHECK_MODULES", (*desktop_main._SELF_CHECK_MODULES, "locus_missing_mod")
    )
    assert desktop_main.self_check() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["modules"]["locus_missing_mod"].startswith("error: ModuleNotFoundError")
