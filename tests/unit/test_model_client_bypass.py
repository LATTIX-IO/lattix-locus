"""LOCUS-376: model providers are reached only through the gated model client.

An AST scan of the repository's Python code (tests excluded). Constructing a
provider SDK client -- ``openai.OpenAI``, ``anthropic.Anthropic``, LangChain chat
models, the Google GenAI client -- or calling a known model API host over HTTP is
allowed only in ``locus_runtime/model_client.py`` (the gateway ``model_call``
PEP) and ``locus_runtime/harness/deep_agents/`` (the Deep Agents runtime, which
builds its chat models behind the same gate). Anything else needs an explicit
entry in ``ALLOWED_EXCEPTIONS`` with a reason.

Dynamic evasions are caught too: ``importlib.import_module("langchain_openai")``
and ``getattr(module, "ChatOpenAI")`` -- the pattern the removed framework chat
adapters used to build ``ChatOpenAI`` outside the gateway (LOCUS-352).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Roots scanned (relative to the repo). Test code is excluded below.
SCAN_ROOTS = ("apps", "locus_runtime", "locus_tooling", "scripts", "packages", "install")
_SKIP_PARTS = {"tests", "node_modules", ".venv", "__pycache__", "frontend", "desktop-tauri"}

#: Paths (files or directories) where provider clients may be built.
ALLOWED_PATHS = (
    "locus_runtime/model_client.py",
    "locus_runtime/harness/deep_agents/",
)

#: (path, qualified function) -> why it may build a provider client directly.
ALLOWED_EXCEPTIONS: dict[tuple[str, str], str] = {
    (
        "locus_runtime/harness/llm.py",
        "OpenAIChatClient._ensure_client",
    ): (
        "ungated client for the evaluation harness (apps/evals), which drives a model "
        "under test outside a platform run; production uses GatedChatClient"
    ),
    # LOCUS-378 closed (LOCUS-387): memory embeddings go through
    # locus_runtime.memory.embedder.GatedEmbedder -> ModelClient.embed (gated).
}

#: Fully qualified provider client constructors / factories.
PROVIDER_CONSTRUCTORS = frozenset(
    {
        "openai.OpenAI",
        "openai.AsyncOpenAI",
        "openai.AzureOpenAI",
        "openai.AsyncAzureOpenAI",
        "anthropic.Anthropic",
        "anthropic.AsyncAnthropic",
        "anthropic.AnthropicBedrock",
        "anthropic.AsyncAnthropicBedrock",
        "anthropic.AnthropicVertex",
        "anthropic.AsyncAnthropicVertex",
        "langchain_openai.ChatOpenAI",
        "langchain_openai.AzureChatOpenAI",
        "langchain_openai.OpenAIEmbeddings",
        "langchain_anthropic.ChatAnthropic",
        "langchain_google_genai.ChatGoogleGenerativeAI",
        "langchain_google_genai.GoogleGenerativeAIEmbeddings",
        "langchain_ollama.ChatOllama",
        "langchain.chat_models.init_chat_model",
        "google.genai.Client",
        "google.generativeai.GenerativeModel",
    }
)
#: Class names that, looked up by string, resolve a provider client dynamically.
_CONSTRUCTOR_NAMES = frozenset(name.rsplit(".", 1)[1] for name in PROVIDER_CONSTRUCTORS)
#: Provider SDK modules; importing one by string is how dynamic bypasses start.
PROVIDER_MODULES = frozenset(
    {
        "openai",
        "anthropic",
        "langchain_openai",
        "langchain_anthropic",
        "langchain_google_genai",
        "langchain_ollama",
        "google.genai",
        "google.generativeai",
        "semantic_kernel",
        "autogen",
        "autogen_agentchat",
        "autogen_ext",
    }
)
#: Hosted model APIs. A literal one inside an HTTP call is a direct model call.
MODEL_HOSTS = (
    "api.openai.com",
    "openai.azure.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "integrate.api.nvidia.com",
    "api.mistral.ai",
    "api.x.ai",
    "api.groq.com",
    "api.together.xyz",
    "openrouter.ai",
    "api.cohere.ai",
    "api.cohere.com",
    "api.deepseek.com",
    "bedrock-runtime",
)
_HTTP_MODULES = frozenset({"httpx", "requests", "urllib", "urllib3", "aiohttp"})
_HTTP_METHODS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "request",
        "stream",
        "send",
        "build_request",
        "urlopen",
    }
)


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> fully qualified name, from every import in the module."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    head = alias.name.split(".", 1)[0]
                    out.setdefault(head, head)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _resolve(name: str, aliases: dict[str, str]) -> str:
    head, _, rest = name.partition(".")
    base = aliases.get(head, head)
    return f"{base}.{rest}" if rest else base


def _strings(node: ast.AST) -> Iterator[str]:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _violations_in_call(call: ast.Call, aliases: dict[str, str]) -> list[str]:
    found: list[str] = []
    name = _resolve(_dotted(call.func), aliases)
    if name in PROVIDER_CONSTRUCTORS:
        found.append(f"constructs {name}")
    first = call.args[0] if call.args else None
    literal = first.value if isinstance(first, ast.Constant) else None
    if name in {"importlib.import_module", "__import__"} and isinstance(literal, str):
        if literal in PROVIDER_MODULES or literal.split(".", 1)[0] in PROVIDER_MODULES:
            found.append(f"imports provider SDK '{literal}' dynamically")
    if name == "getattr" and len(call.args) >= 2:
        attr = call.args[1]
        if isinstance(attr, ast.Constant) and attr.value in _CONSTRUCTOR_NAMES:
            found.append(f"resolves '{attr.value}' dynamically")
    root = name.split(".", 1)[0]
    method = name.rsplit(".", 1)[-1]
    is_http = root in _HTTP_MODULES or (
        isinstance(call.func, ast.Attribute) and method in _HTTP_METHODS
    )
    if is_http:
        args: list[ast.AST] = [*call.args, *(kw.value for kw in call.keywords)]
        for arg in args:
            hosts = {host for text in _strings(arg) for host in MODEL_HOSTS if host in text}
            if hosts:
                found.append(f"HTTP call to model host {sorted(hosts)}")
    return found


def _functions(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """(qualified name, node) for module level, functions and methods."""

    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    yield qualname, child
                yield from walk(child, f"{qualname}.")

    yield "<module>", tree
    yield from walk(tree, "")


def scan_source(source: str) -> list[tuple[str, str]]:
    """(qualified function, violation) for every provider-client bypass in ``source``."""
    tree = ast.parse(source)
    aliases = _aliases(tree)
    owner: dict[int, str] = {}
    for qualname, func in _functions(tree):
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                owner[id(node)] = qualname  # innermost function wins (walked last)
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for violation in _violations_in_call(node, aliases):
                out.append((owner.get(id(node), "<module>"), violation))
    return out


def _scanned_files() -> Iterator[Path]:
    for root in SCAN_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(REPO)
            if _SKIP_PARTS & set(rel.parts[:-1]) or rel.name.startswith("test_"):
                continue
            yield path


def _is_allowed_path(rel: str) -> bool:
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in ALLOWED_PATHS)


def test_provider_clients_are_built_only_behind_the_gateway() -> None:
    violations: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(REPO).as_posix()
        if _is_allowed_path(rel):
            continue
        for qualname, violation in scan_source(path.read_text(encoding="utf-8")):
            if (rel, qualname) not in ALLOWED_EXCEPTIONS:
                violations.append(f"{rel}:{qualname} {violation}")
    assert not violations, (
        "model provider reached outside locus_runtime.model_client (LOCUS-376); route "
        "it through the gated model client or add an ALLOWED_EXCEPTIONS entry with a "
        "reason:\n" + "\n".join(violations)
    )


def test_allowed_exceptions_are_still_needed() -> None:
    """Keep the allowlist honest: each entry names a function that still bypasses."""
    for (rel, qualname), reason in ALLOWED_EXCEPTIONS.items():
        assert reason.strip(), (rel, qualname)
        found = scan_source((REPO / rel).read_text(encoding="utf-8"))
        assert any(owner == qualname for owner, _ in found), f"stale exception: {rel}:{qualname}"


def test_allowed_paths_exist() -> None:
    for allowed in ALLOWED_PATHS:
        assert (REPO / allowed).exists(), allowed


def test_scanner_catches_direct_aliased_and_dynamic_constructions() -> None:
    source = """
import importlib
import httpx
import openai
from anthropic import Anthropic as Claude
from google import genai
from langchain_openai import ChatOpenAI


def direct():
    return openai.OpenAI(api_key="k"), openai.AsyncOpenAI()


def aliased():
    return Claude(), genai.Client(), ChatOpenAI(model="m")


def dynamic():
    module = importlib.import_module("langchain_openai")
    return getattr(module, "ChatOpenAI")(model="m")


def raw_http(key):
    httpx.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": key})
    with httpx.Client() as client:
        client.post(f"https://api.openai.com/v1/{key}")


class Holder:
    def build(self):
        return openai.AzureOpenAI()


def fine(base_url):
    httpx.get("http://127.0.0.1:11434/api/tags")
    httpx.post(base_url)
    defaults = {"openai": "https://api.openai.com/v1"}
    return defaults
"""
    found = scan_source(source)
    owners = {owner for owner, _ in found}
    assert owners == {"direct", "aliased", "dynamic", "raw_http", "Holder.build"}
    messages = [message for _, message in found]
    for expected in (
        "constructs openai.OpenAI",
        "constructs openai.AsyncOpenAI",
        "constructs anthropic.Anthropic",
        "constructs google.genai.Client",
        "constructs langchain_openai.ChatOpenAI",
        "imports provider SDK 'langchain_openai' dynamically",
        "resolves 'ChatOpenAI' dynamically",
        "HTTP call to model host ['api.anthropic.com']",
        "HTTP call to model host ['api.openai.com']",
        "constructs openai.AzureOpenAI",
    ):
        assert expected in messages, expected
