"""Static review for the D-29 provenance inspection (a small semgrep-style ruleset).

The rules look for what D-29 names: telemetry and analytics SDKs, hidden or
obfuscated network calls, install-time code, and native code. Python sources are
parsed with :mod:`ast` (so strings and comments do not trip import rules);
everything else is matched with regular expressions over bytes. Native binaries
are flagged for review and scanned for networking imports/symbols.

Findings carry a default ``disposition``: ``needs-review`` for ``review``/``high``
findings and ``benign`` for ``info``. A reviewer may downgrade a finding with a
``note``; nothing here decides that code is safe. Pure apart from reading files.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RULESET = "locus-provenance-static/1"

#: Telemetry, analytics and crash-reporting SDKs (top-level module names).
TELEMETRY_MODULES = frozenset(
    {
        "sentry_sdk",
        "raven",
        "posthog",
        "mixpanel",
        "amplitude",
        "analytics",  # segment analytics-python
        "segment",
        "rudderstack",
        "rudder_analytics",
        "datadog",
        "ddtrace",
        "newrelic",
        "bugsnag",
        "rollbar",
        "honeybadger",
        "statsd",
        "logzio",
        "scarf",
        "heap",
        "keen",
        "snowplow_tracker",
        "countly",
        "matomo",
        "umami",
        "plausible",
        "opentelemetry",
        "applicationinsights",
        "opencensus",
        "azure_monitor",
    }
)
#: Module names that hint at telemetry even when the SDK is unknown.
_TELEMETRY_NAME = re.compile(r"(telemetry|analytics|tracking|tracker|usage_stats|phone_home)", re.I)

#: Network clients and raw sockets (module prefixes).
NETWORK_MODULES = (
    "socket",
    "ssl",
    "urllib.request",
    "urllib3",
    "http.client",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc.client",
    "pycurl",
    "grpc",
    "paramiko",
    "dns",
)
_NETWORK_CALLS = {
    ("asyncio", "open_connection"),
    ("asyncio", "create_connection"),
    ("urllib", "urlopen"),
    ("webbrowser", "open"),
}
_PROCESS_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "spawnv"),
    ("os", "startfile"),
}
_DECODERS = {
    "b64decode",
    "b32decode",
    "b16decode",
    "a85decode",
    "b85decode",
    "decodebytes",
    "decompress",
    "unhexlify",
    "fromhex",
    "loads",
    "rot13",
    "decode",
}
_DYNAMIC_EXEC = {"exec", "eval", "compile", "__import__"}
_INSTALL_COMMANDS = {
    "install",
    "develop",
    "egg_info",
    "build_py",
    "build_ext",
    "sdist",
    "bdist_wheel",
}

NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")
#: Networking imports/symbols in native code (PE import names, ELF/Mach-O symbols).
_NATIVE_NETWORK = re.compile(
    rb"(ws2_32\.dll|wsock32\.dll|winhttp\.dll|wininet\.dll|WSAStartup|WinHttpOpen|"
    rb"InternetOpen[AW]?|URLDownloadToFile|getaddrinfo|gethostbyname|curl_easy_init|"
    rb"SSL_connect|CFNetwork|NSURLSession)",
    re.I,
)
_LONG_BASE64 = re.compile(rb"[A-Za-z0-9+/]{200,}={0,2}")
_TEXT_SUFFIXES = (".py", ".pyi", ".pth", ".cfg", ".toml", ".txt", ".json", ".js", ".sh", ".ps1")
_MAX_TEXT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # info | review | high
    path: str
    line: int
    detail: str
    disposition: str = ""

    def as_record(self) -> dict[str, Any]:
        data = asdict(self)
        data["disposition"] = self.disposition or (
            "benign" if self.severity == "info" else "needs-review"
        )
        return data


@dataclass(frozen=True)
class ScanResult:
    files_scanned: int
    findings: tuple[Finding, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "ruleset": RULESET,
            "files_scanned": self.files_scanned,
            "findings": [f.as_record() for f in self.findings],
        }


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _is_literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


_DECODER_MODULES = (
    "base64.",
    "zlib.",
    "marshal.",
    "codecs.",
    "binascii.",
    "lzma.",
    "bz2.",
    "gzip.",
)
#: Leaf names too common to mean "decoder" on their own (``bytes.decode``, ``json.loads``).
_AMBIGUOUS_DECODERS = frozenset({"decode", "loads"})


def _contains_decoder(node: ast.AST) -> str:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _dotted(child.func)
            leaf = name.rsplit(".", 1)[-1]
            if name.startswith(_DECODER_MODULES) or (
                leaf in _DECODERS and leaf not in _AMBIGUOUS_DECODERS
            ):
                return name or leaf
    return ""


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, rel: str, is_setup: bool) -> None:
        self.rel = rel
        self.is_setup = is_setup
        self.findings: list[Finding] = []

    def _add(self, rule: str, severity: str, node: ast.AST, detail: str) -> None:
        self.findings.append(
            Finding(rule, severity, self.rel, int(getattr(node, "lineno", 0)), detail)
        )

    def _check_module(self, module: str, node: ast.AST) -> None:
        top = module.split(".", 1)[0]
        if top in TELEMETRY_MODULES:
            self._add(
                "telemetry-sdk-import", "high", node, f"imports telemetry/analytics SDK '{module}'"
            )
        elif _TELEMETRY_NAME.search(module):
            self._add("telemetry-like-import", "review", node, f"imports '{module}'")
        for prefix in NETWORK_MODULES:
            if module == prefix or module.startswith(prefix + "."):
                self._add(
                    "network-client-import", "review", node, f"imports network module '{module}'"
                )
                break
        if top == "subprocess":
            self._add("process-spawn-import", "review", node, "imports subprocess")
        if top == "ctypes":
            self._add(
                "ctypes-import",
                "review",
                node,
                "imports ctypes (native calls bypass Python audit hooks)",
            )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self._check_module(node.module, node)
            for alias in node.names:
                pair = (node.module, alias.name)
                if pair in _NETWORK_CALLS or (
                    node.module == "urllib.request" and alias.name == "urlopen"
                ):
                    self._add("network-call", "review", node, f"imports {node.module}.{alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        leaf = name.rsplit(".", 1)[-1]
        if name in _DYNAMIC_EXEC or (leaf in {"exec", "eval"} and name.startswith("builtins.")):
            args = list(node.args)
            if args and not _is_literal(args[0]):
                decoder = _contains_decoder(args[0])
                if decoder:
                    self._add(
                        "obfuscated-exec",
                        "high",
                        node,
                        f"{leaf}() of a value produced by {decoder}() (decode-then-execute)",
                    )
                else:
                    self._add("dynamic-exec", "review", node, f"{leaf}() of a non-literal value")
        if name == "getattr" and len(node.args) >= 2:
            target = node.args[1]
            if isinstance(target, ast.Constant) and target.value in _DYNAMIC_EXEC:
                self._add(
                    "obfuscated-exec",
                    "high",
                    node,
                    f"getattr(..., {target.value!r}) hides a dynamic exec",
                )
        if name in {"marshal.loads", "pickle.loads"} and node.args and _is_literal(node.args[0]):
            self._add("embedded-code-object", "high", node, f"{name}() of embedded bytes")
        parts = tuple(name.split(".")[-2:]) if "." in name else ()
        if parts in _PROCESS_CALLS:
            self._add("process-spawn", "review", node, f"calls {name}()")
        if parts in _NETWORK_CALLS or name.endswith("urlopen"):
            self._add("network-call", "review", node, f"calls {name}()")
        if self.is_setup and leaf == "setup":
            for keyword in node.keywords:
                if keyword.arg == "cmdclass":
                    self._add(
                        "install-hook-cmdclass",
                        "high",
                        keyword.value,
                        "setup(cmdclass=...) runs custom code at build/install time",
                    )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.is_setup:
            for base in node.bases:
                leaf = _dotted(base).rsplit(".", 1)[-1]
                if leaf in _INSTALL_COMMANDS:
                    self._add(
                        "install-hook-command",
                        "high",
                        node,
                        f"class {node.name} overrides setuptools command '{leaf}'",
                    )
        self.generic_visit(node)


def scan_python_source(source: str, rel: str) -> list[Finding]:
    name = Path(rel).name
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [
            Finding(
                "unparseable-python",
                "review",
                rel,
                int(exc.lineno or 0),
                f"cannot parse: {exc.msg}",
            )
        ]
    visitor = _PythonVisitor(rel, is_setup=name == "setup.py")
    visitor.visit(tree)
    return visitor.findings


def scan_pth(text: str, rel: str) -> list[Finding]:
    """``.pth`` files run their ``import`` lines at every interpreter start."""
    findings = [Finding("pth-file", "review", rel, 0, ".pth file (adds to sys.path at startup)")]
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("import ", "import\t")):
            findings.append(
                Finding(
                    "pth-exec",
                    "high",
                    rel,
                    number,
                    "executable .pth line runs code at interpreter start",
                )
            )
    return findings


def scan_native(data: bytes, rel: str) -> list[Finding]:
    findings = [
        Finding(
            "native-extension",
            "review",
            rel,
            0,
            "native binary: needs review (opaque to the AST rules)",
        )
    ]
    hits = sorted(
        {m.group(0).decode("ascii", "replace").lower() for m in _NATIVE_NETWORK.finditer(data)}
    )
    if hits:
        findings.append(
            Finding(
                "native-network-symbols",
                "high",
                rel,
                0,
                f"networking imports/symbols: {', '.join(hits)}",
            )
        )
    return findings


def _iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def scan_tree(root: str | Path, *, label: str = "") -> ScanResult:
    """Run every rule over the files under ``root`` (an unpacked wheel or sdist)."""
    base = Path(root)
    findings: list[Finding] = []
    count = 0
    for path in _iter_files(base):
        count += 1
        rel = (f"{label}/" if label else "") + path.relative_to(base).as_posix()
        suffix = path.suffix.lower()
        if suffix in NATIVE_SUFFIXES or ".so." in path.name:
            findings += scan_native(path.read_bytes(), rel)
            continue
        if suffix not in _TEXT_SUFFIXES or path.stat().st_size > _MAX_TEXT_BYTES:
            continue
        data = path.read_bytes()
        if suffix in {".py", ".pyi"}:
            findings += scan_python_source(data.decode("utf-8", "replace"), rel)
        elif suffix == ".pth":
            findings += scan_pth(data.decode("utf-8", "replace"), rel)
        for match in _LONG_BASE64.finditer(data):
            line = data.count(b"\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    "encoded-blob",
                    "review",
                    rel,
                    line,
                    f"{len(match.group(0))}-char base64-like literal",
                )
            )
            break
    return ScanResult(count, tuple(findings))


def merge(results: Iterable[ScanResult]) -> ScanResult:
    files = 0
    findings: list[Finding] = []
    for result in results:
        files += result.files_scanned
        findings += result.findings
    return ScanResult(files, tuple(findings))
