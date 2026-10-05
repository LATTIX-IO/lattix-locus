"""LOCUS-358 / D-29: the static ruleset on benign and malicious fixture packages.

The fixtures are written to tmp_path at test time (never imported or run), so the
repository carries no executable "malicious" sample.
"""

from __future__ import annotations

import base64
import io
import tarfile
from collections.abc import Mapping
from pathlib import Path

from locus_tooling.provenance import static_rules
from locus_tooling.provenance.inspection import source_match


def _tree(root: Path, files: Mapping[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_bytes(content.encode("utf-8"))
    return root


def _rules(result: static_rules.ScanResult) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for finding in result.findings:
        out.setdefault(finding.rule, set()).add(finding.path)
    return out


BENIGN: dict[str, str | bytes] = {
    "goodpkg/__init__.py": (
        '"""Parses things. Mentions socket, requests and exec() only in prose."""\n'
        "import json\n"
        "import re\n\n"
        "PATTERN = re.compile(r'[a-z]+')\n\n\n"
        "def parse(text: str) -> dict:\n"
        "    return json.loads(text)\n\n\n"
        "def literal() -> int:\n"
        "    return eval('1 + 1')\n"
    ),
    "goodpkg/py.typed": "",
    "goodpkg-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: goodpkg\nVersion: 1.0\n",
}

ENCODED = base64.b64encode(b"print('payload')").decode()
MALICIOUS: dict[str, str | bytes] = {
    "badpkg/__init__.py": (
        "import base64\n"
        "import builtins\n"
        "import socket\n"
        "import sentry_sdk\n"
        "import requests\n"
        "from urllib.request import urlopen\n\n"
        f"exec(base64.b64decode('{ENCODED}'))\n"
        "getattr(builtins, 'exec')('pass')\n"
        "socket.create_connection(('203.0.113.9', 443))\n"
    ),
    "badpkg/loader.py": "import marshal\ncode = marshal.loads(b'\\xe3\\x00')\nimport os\nos.system('true')\n",
    "badpkg/blob.py": "DATA = '" + "QUJD" * 80 + "'\n",
    "badpkg/_native.cpython-312-x86_64-linux-gnu.so": b"\x7fELF....getaddrinfo\x00connect\x00",
    "badpkg/helper.pyd": b"MZ....WS2_32.dll\x00WSAStartup\x00",
    "zz_badpkg.pth": "import badpkg\n",
    "setup.py": (
        "from setuptools import setup\n"
        "from setuptools.command.install import install\n\n\n"
        "class PostInstall(install):\n"
        "    def run(self):\n"
        "        install.run(self)\n\n\n"
        "setup(name='badpkg', cmdclass={'install': PostInstall})\n"
    ),
}


def test_benign_package_has_no_findings_beyond_info(tmp_path: Path) -> None:
    result = static_rules.scan_tree(_tree(tmp_path, BENIGN))
    assert result.files_scanned == 3
    assert [f for f in result.findings if f.severity != "info"] == []


def test_malicious_package_trips_every_rule_family(tmp_path: Path) -> None:
    result = static_rules.scan_tree(_tree(tmp_path, MALICIOUS), label="badpkg-1.0.tar.gz")
    rules = _rules(result)
    expected = {
        "telemetry-sdk-import",
        "network-client-import",
        "network-call",
        "obfuscated-exec",
        "embedded-code-object",
        "process-spawn",
        "encoded-blob",
        "native-extension",
        "native-network-symbols",
        "pth-file",
        "pth-exec",
        "install-hook-cmdclass",
        "install-hook-command",
    }
    assert expected <= set(rules), sorted(expected - set(rules))
    assert all(path.startswith("badpkg-1.0.tar.gz/") for paths in rules.values() for path in paths)
    obfuscated = [f for f in result.findings if f.rule == "obfuscated-exec"]
    assert len(obfuscated) == 2 and {f.severity for f in obfuscated} == {"high"}
    assert {f.line for f in obfuscated} == {8, 9}


def test_default_dispositions_need_review_for_non_info(tmp_path: Path) -> None:
    result = static_rules.scan_tree(_tree(tmp_path, MALICIOUS))
    for item in result.as_record()["findings"]:
        assert item["disposition"] == ("benign" if item["severity"] == "info" else "needs-review")


def test_unparseable_python_is_flagged(tmp_path: Path) -> None:
    result = static_rules.scan_tree(_tree(tmp_path, {"broken.py": "def (:\n"}))
    assert _rules(result) == {"unparseable-python": {"broken.py"}}


def _tarball(files: dict[str, bytes], prefix: str = "repo-1.0") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_source_match_flags_wheel_files_that_differ_from_the_tag(tmp_path: Path) -> None:
    tag = _tarball({"pkg/__init__.py": b"A = 1\n", "pkg/util.py": b"B = 2\n", "README": b"r"})
    wheel = _tree(
        tmp_path / "pkg-1.0-py3-none-any.whl",
        {
            "pkg/__init__.py": "A = 1\n",
            "pkg/util.py": "B = 3\n",
            "pkg/extra.py": "C = 4\n",
            "pkg-1.0.dist-info/RECORD": "",
        },
    )
    sdist = _tree(
        tmp_path / "pkg-1.0.tar.gz",
        {"pkg-1.0/pkg/__init__.py": "A = 1\n", "pkg-1.0/PKG-INFO": "x", "pkg-1.0/README": "r"},
    )
    match = source_match(
        "example/pkg",
        "v1.0",
        ["pkg"],
        {"pkg-1.0-py3-none-any.whl": wheel, "pkg-1.0.tar.gz": sdist},
        tarball=tag,
    )
    rules = {(f.rule, f.path) for f in match.scan.findings}
    assert ("source-mismatch", "pkg-1.0-py3-none-any.whl/pkg/util.py") in rules
    assert ("file-not-in-source", "pkg-1.0-py3-none-any.whl/pkg/extra.py") in rules
    assert ("source-build-metadata", "pkg-1.0.tar.gz/PKG-INFO") in rules
    assert "pkg-1.0-py3-none-any.whl: 1/3 files identical" in match.summary
    assert "pkg-1.0.tar.gz: 2/3 files identical" in match.summary
