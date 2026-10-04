"""Model gate for D-29: weights-only format check, the behavioural-eval hook, and
the runtime lookup that lets a listed-lineage model run on a local engine.

* **Format check.** Only safetensors and GGUF weights pass. Pickle-based files
  (``.bin``, ``.pt``, ``.pth``, ``.ckpt``, ``.pkl``, ...), anything whose bytes
  look like a pickle or a torch zip, and any custom loader code (``*.py``,
  native libraries, ``auto_map`` / ``trust_remote_code`` in the configs) are
  refused. The magic bytes are checked, not just the extension.
* **Behavioural / red-team eval.** :func:`run_behavioural_eval` is a hook; until
  LOCUS-351 installs a real suite it records ``pending``, and a model attestation
  with a pending eval never passes (D-29: "a behavioural and red-team eval before
  use").
* **Runtime lookup.** :func:`local_model_verdict` is what the model client calls
  for a listed-lineage model on a loopback engine. Today only Ollama is
  attestable: its manifest names the weights blob by SHA-256, so the attested
  digest can be checked against what the engine will actually load. Anything that
  cannot be bound to attested weights is refused (fail closed).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .records import Verdict, lookup

WEIGHT_FORMATS = {".safetensors": "safetensors", ".gguf": "gguf"}
PICKLE_EXTENSIONS = frozenset(
    {".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle", ".joblib", ".npy", ".npz", ".mar", ".nemo"}
)
CODE_EXTENSIONS = frozenset(
    {".py", ".pyc", ".pyd", ".so", ".dll", ".dylib", ".sh", ".js", ".ps1", ".bat"}
)
#: Non-weight files a model repository legitimately carries.
METADATA_EXTENSIONS = frozenset(
    {
        ".json",
        ".txt",
        ".md",
        ".model",
        ".tiktoken",
        ".jinja",
        ".yaml",
        ".yml",
        ".gitattributes",
        ".png",
        ".jpg",
    }
)
METADATA_NAMES = frozenset({"license", "notice", "readme", "use_policy"})
_REMOTE_CODE_KEYS = ("auto_map", "trust_remote_code")

OLLAMA_REGISTRY = "registry.ollama.ai"
OLLAMA_WEIGHT_MEDIA = frozenset(
    {
        "application/vnd.ollama.image.model",
        "application/vnd.ollama.image.projector",
        "application/vnd.ollama.image.adapter",
    }
)
BEHAVIOURAL_EVAL_TRACKING = "LOCUS-351"


@dataclass
class FormatCheck:
    weights: list[dict[str, str]] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "pass" if self.weights and not self.refused else "fail"

    def as_record(self) -> dict[str, Any]:
        return {"status": self.status, "weights": self.weights, "refused": self.refused}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sniff(path: Path) -> str:
    """``safetensors`` | ``gguf`` | ``pickle`` | ``torch-zip`` | ``unknown`` from magic bytes."""
    with path.open("rb") as handle:
        head = handle.read(16)
    if head[:4] == b"GGUF":
        return "gguf"
    if len(head) >= 2 and head[0] == 0x80 and head[1] in (2, 3, 4, 5):
        return "pickle"
    if head[:2] == b"PK":
        try:
            with zipfile.ZipFile(path) as archive:
                if any(n.endswith(("data.pkl", ".pkl")) for n in archive.namelist()):
                    return "torch-zip"
        except zipfile.BadZipFile:
            return "unknown"
        return "unknown"
    if len(head) >= 9:
        size = int.from_bytes(head[:8], "little")
        if 2 <= size <= min(100 * 1024 * 1024, path.stat().st_size - 8) and head[8:9] == b"{":
            return "safetensors"
    return "unknown"


def _config_has_remote_code(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    text = json.dumps(data)
    return any(f'"{key}"' in text for key in _REMOTE_CODE_KEYS)


def check_weights(paths: Iterable[Path], *, base: Path | None = None) -> FormatCheck:
    """Weights-only check over model files (a directory's files or single blobs)."""
    result = FormatCheck()
    for path in sorted(paths):
        rel = path.relative_to(base).as_posix() if base else path.name
        suffix = path.suffix.lower()
        stem = path.stem.lower()
        if suffix in CODE_EXTENSIONS:
            result.refused.append({"path": rel, "reason": "custom loader code is not allowed"})
            continue
        if suffix in PICKLE_EXTENSIONS:
            result.refused.append(
                {"path": rel, "reason": f"pickle-capable format '{suffix}' is refused"}
            )
            continue
        if suffix == ".json" and _config_has_remote_code(path):
            result.refused.append(
                {
                    "path": rel,
                    "reason": "config requests custom code (auto_map / trust_remote_code)",
                }
            )
            continue
        if (
            suffix in METADATA_EXTENSIONS
            or stem in METADATA_NAMES
            or path.name.lower() in METADATA_NAMES
        ):
            kind = sniff(path)
            if kind in {"pickle", "torch-zip"}:
                result.refused.append(
                    {"path": rel, "reason": f"{kind} content under a metadata name"}
                )
            continue
        kind = sniff(path)
        declared = WEIGHT_FORMATS.get(suffix)
        if declared and kind == declared:
            result.weights.append({"path": rel, "format": declared, "sha256": _sha256(path)})
        elif declared is None and suffix == "" and kind in {"gguf", "safetensors"}:
            # Content-addressed blobs (Ollama) have no extension: trust the magic.
            result.weights.append({"path": rel, "format": kind, "sha256": _sha256(path)})
        else:
            result.refused.append(
                {
                    "path": rel,
                    "reason": f"not a recognized weights-only file (content looks like {kind})",
                }
            )
    return result


def check_model_dir(root: Path) -> FormatCheck:
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    return check_weights(files, base=root)


# --------------------------------------------------------------------------- #
# Behavioural / red-team eval hook (LOCUS-351)
# --------------------------------------------------------------------------- #
BehaviouralEval = Callable[[str], dict[str, Any]]
_EVAL_HOOK: list[BehaviouralEval] = []


def install_behavioural_eval(hook: BehaviouralEval | None) -> None:
    """LOCUS-351 installs the real suite here; ``None`` restores the pending stub."""
    _EVAL_HOOK.clear()
    if hook is not None:
        _EVAL_HOOK.append(hook)


def run_behavioural_eval(model_ref: str) -> dict[str, Any]:
    if _EVAL_HOOK:
        return dict(_EVAL_HOOK[0](model_ref))
    return {
        "status": "pending",
        "tracking": BEHAVIOURAL_EVAL_TRACKING,
        "date": _dt.date.today().isoformat(),
        "notes": "No behavioural/red-team suite is installed yet; the model cannot pass until it runs.",
    }


# --------------------------------------------------------------------------- #
# Ollama: names, manifests, runtime verdict
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OllamaRef:
    registry: str
    namespace: str
    model: str
    tag: str

    @property
    def attestation_name(self) -> str:
        """``model`` for the official library, ``namespace/model`` otherwise."""
        base = self.model if self.namespace == "library" else f"{self.namespace}/{self.model}"
        return base if self.registry == OLLAMA_REGISTRY else f"{self.registry}/{base}"


_OLLAMA_PART = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.I)


def parse_ollama_ref(model: str) -> OllamaRef | None:
    text = str(model or "").strip()
    name, _, tag = text.partition(":")
    tag = tag or "latest"
    parts = [p for p in name.split("/") if p]
    if not parts or len(parts) > 3:
        return None
    registry, namespace = OLLAMA_REGISTRY, "library"
    if len(parts) == 3:
        registry, namespace, model_name = parts
    elif len(parts) == 2:
        namespace, model_name = parts
    else:
        model_name = parts[0]
    if not all(_OLLAMA_PART.match(p) for p in (namespace, model_name, tag)):
        return None
    if not re.match(r"^[a-z0-9.-]+(:\d+)?$", registry, re.I):
        return None
    return OllamaRef(registry, namespace, model_name, tag)


def ollama_models_dir() -> Path:
    configured = str(os.getenv("OLLAMA_MODELS") or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".ollama" / "models"


def ollama_weight_digests(ref: OllamaRef, models_dir: Path | None = None) -> list[str] | None:
    """SHA-256 digests of the weights layers the engine will load, or ``None``."""
    root = models_dir or ollama_models_dir()
    manifest = root / "manifests" / ref.registry / ref.namespace / ref.model / ref.tag
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    digests = []
    for layer in data.get("layers") or []:
        if isinstance(layer, dict) and layer.get("mediaType") in OLLAMA_WEIGHT_MEDIA:
            digest = str(layer.get("digest") or "")
            if not digest.startswith("sha256:"):
                return None
            digests.append(digest.split(":", 1)[1].lower())
    return digests or None


def ollama_blob_path(digest: str, models_dir: Path | None = None) -> Path:
    return (models_dir or ollama_models_dir()) / "blobs" / f"sha256-{digest}"


def local_model_verdict(
    provider: str,
    model: str,
    *,
    roots: Iterable[str | Path] | None = None,
    models_dir: Path | None = None,
) -> Verdict:
    """Whether a listed-lineage ``model`` may run on the local ``provider`` (fail closed)."""
    if provider != "ollama":
        return Verdict(
            False,
            (
                f"local engine '{provider}' cannot bind a model to attested weights; "
                "only Ollama models are attestable at runtime",
            ),
        )
    ref = parse_ollama_ref(model)
    if ref is None:
        return Verdict(False, (f"unrecognized Ollama model reference '{model}'",))
    verdict, record = lookup(
        "ollama", ref.attestation_name, ref.tag, roots=list(roots) if roots is not None else None
    )
    if not verdict.passing or record is None:
        return verdict
    if record.get("kind") != "model":
        return Verdict(False, ("the attestation is not a model attestation",))
    digests = ollama_weight_digests(ref, models_dir)
    if digests is None:
        return Verdict(
            False,
            (f"cannot read the Ollama manifest for {model}, so its weights cannot be verified",),
        )
    attested = {str(a.get("sha256", "")).lower() for a in record.get("artifacts") or []}
    unattested = [d for d in digests if d not in attested]
    if unattested:
        return Verdict(
            False,
            (f"the installed weights for {model} differ from the attested artifacts",),
        )
    return Verdict(True, ())
