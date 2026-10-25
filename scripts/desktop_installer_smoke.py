"""Inspect the actual Windows NSIS installer, not only the staged frontend.

LOCUS-475: the installed v0.10.4 served /home HTML but six referenced JS/CSS
chunks were absent from resources/frontend/.next/static/chunks. This check
extracts the unsigned/signed NSIS build made in CI, compares every static file
with the independently smoke-checked stage, then serves the extracted Next.js
bundle using the Node executable included in that same installer.

Do not execute the installer or mutate the host application state. The trusted
release workflows and the secretless Windows PR build both run this check
before any published artifact is accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from desktop_frontend_smoke import smoke


class InstallerSmokeError(RuntimeError):
    """Installer resource verification failed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path) -> dict[str, tuple[int, str]]:
    if not root.is_dir():
        raise InstallerSmokeError(f"frontend static directory absent: {root}")
    result: dict[str, tuple[int, str]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if name in result:
                raise InstallerSmokeError(f"duplicate static relative file: {name}")
            result[name] = (path.stat().st_size, _sha256(path))
            if len(result) > 20_000:
                raise InstallerSmokeError("unexpectedly large frontend static tree")
    if not result or not any(p.endswith(".js") for p in result):
        raise InstallerSmokeError("frontend static tree contains no JavaScript")
    return result


def verify_extracted_frontend(staged: Path, extracted: Path) -> int:
    """Fail on a missing/renamed/modified byte in the NSIS frontend payload."""
    staged = staged.resolve()
    extracted = extracted.resolve()
    for relative in ("server.js", ".next/BUILD_ID"):
        a, b = staged / relative, extracted / relative
        if not a.is_file() or not b.is_file() or _sha256(a) != _sha256(b):
            raise InstallerSmokeError(f"installer frontend mismatch: {relative}")
    staged_static = staged / ".next" / "static"
    extracted_static = extracted / ".next" / "static"
    if (extracted_static / "static").exists():
        raise InstallerSmokeError("nested .next/static/static tree in installer")
    expected = _files(staged_static)
    actual = _files(extracted_static)
    expected_keys, actual_keys = set(expected), set(actual)
    if expected_keys != actual_keys:
        missing = sorted(expected_keys - actual_keys)[:6]
        extra = sorted(actual_keys - expected_keys)[:6]
        raise InstallerSmokeError(
            f"NSIS static manifest differs from staged frontend: "
            f"missing={missing}, unexpected={extra}"
        )
    bad = [name for name in sorted(expected) if expected[name] != actual[name]]
    if bad:
        raise InstallerSmokeError(f"NSIS frontend content hash mismatch: {bad[:6]}")
    return len(actual)


def _seven_zip() -> str:
    for command in ("7z", "7za", "7zr"):
        found = shutil.which(command)
        if found:
            return found
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    found = program_files / "7-Zip" / "7z.exe"
    if found.is_file():
        return str(found)
    raise InstallerSmokeError("7-Zip CLI required to inspect the Windows NSIS installer")


def _frontend_in_extraction(extracted_tree: Path) -> Path:
    candidates = [
        p.parent for p in extracted_tree.rglob("server.js")
        if len(p.parts) >= 3
        and p.parent.name.lower() == "frontend"
        and p.parent.parent.name.lower() == "resources"
    ]
    if len(candidates) != 1:
        raise InstallerSmokeError(
            f"expected one resources/frontend/server.js in extracted NSIS, "
            f"found {len(candidates)}"
        )
    return candidates[0]


def validate_nsis(installer: Path, staged: Path, *, version: str) -> None:
    installer = installer.resolve()
    staged = staged.resolve()
    if not installer.is_file() or not installer.name.lower().endswith("-setup.exe"):
        raise InstallerSmokeError(f"expected the built NSIS -setup.exe: {installer}")
    with tempfile.TemporaryDirectory(prefix="locus-nsis-verify-") as working:
        dest = Path(working)
        try:
            completed = subprocess.run(
                [_seven_zip(), "x", "-y", f"-o{dest}", str(installer)],
                check=False, capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallerSmokeError("NSIS extraction timed out") from exc
        if completed.returncode != 0:
            raise InstallerSmokeError(
                f"NSIS extraction failed (exit {completed.returncode}): "
                f"{(completed.stderr or completed.stdout)[-800:]}"
            )
        frontend = _frontend_in_extraction(dest)
        count = verify_extracted_frontend(staged, frontend)
        node = frontend.parent.parent / "bin" / "node.exe"
        if not node.is_file():
            raise InstallerSmokeError("NSIS does not include the bundled bin/node.exe")
        print(f"NSIS static file manifest matches staged frontend: {count} files")
        # Serve exactly the extracted resource tree with its packaged Node.
        # This rechecks the real /home page and every referenced JS/CSS asset.
        smoke(frontend, node=str(node), version=version, deadline_seconds=50)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not args.version or len(args.version) > 64:
        parser.error("nonempty bounded version required")
    validate_nsis(args.installer, args.frontend, version=args.version)


if __name__ == "__main__":
    main()
