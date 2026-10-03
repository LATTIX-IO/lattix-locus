"""Biscuit capability grants at the gateway (LOCUS-334, 13 §5, 11 §5).

A *grant* is a Biscuit token (Ed25519, public-key verifiable, attenuable) minted
by the local **grant authority** when a human approves a gateway ``ask`` with a
``run`` or ``standing`` scope. The gateway looks grants up server-side for the
authenticated principal (:class:`GrantStore`) -- a grant is never accepted from
an action, from agent text or from a request body -- and a valid covering grant
turns an R3 (or supervised R2) ``ask`` into ``allow``. Grants never authorize
R4 and never override a policy deny: the gateway consults them only after
policy allowed and only below R4 (see :mod:`locus_runtime.gateway`).

Datalog schema
--------------

Authority block (signed by the grant authority; every value is a parameter)::

    grant_id({id}); grant_principal({principal}); grant_scope({"run"|"standing"});
    grant_kind({kind}); grant_tool({tool}); [grant_run({run_id});] [grant_pinned(true);]
    check if principal({principal});
    check if action({kind}, {tool});
    check if action_target({target});                         // target_mode "exact"
    check if action_target($t), $t.starts_with({prefix});     // target_mode "prefix"
    reject if action_target($t), $t.contains("..");           //   (prefix only)
    check if action_args({args});                             // tool / MCP calls
    check if action_command({command});                       // process_exec
    check if recipients({recipients});  | reject if recipients($r);
    check if amount($a), $a <= {ceiling}; | reject if amount($a);
    check if run({run_id});                                    // run-scoped grants
    check if time($t), $t <= {expires_at};                     // unless pinned

Authorizer (built by :class:`BiscuitGrantVerifier` from the gateway's own
action; never from caller-supplied facts)::

    principal(..); run(..); action(kind, tool); action_target(..);
    [action_args(..)] [action_command(..)] [recipients(..)] [amount(..)]
    time(now); allow if grant_id($g);

Attenuation blocks (:func:`attenuate`) may only add checks: the authorizer's
``allow`` policy trusts the authority block and the authorizer only, so facts
an attenuation block adds are invisible to it and can never widen a grant.

Keys
----

The authority's Ed25519 private key is the secret ``LOCUS_GRANT_AUTHORITY_KEY``
(32 bytes, url-safe base64 or hex). The native launcher provisions it through
:mod:`locus_tooling.native_secrets` (OS keychain / Windows DPAPI; never a
plaintext file by default) and exports it to the backend; the backend resolves
it read-only through the same module. Each token carries a root key id (derived
from the public key); the verifier accepts the current key plus any public keys
listed in ``LOCUS_GRANT_ACCEPTED_PUBLIC_KEYS`` (comma-separated hex) -- that is
how a key is rotated without invalidating live grants. No key → no authority →
the gateway runs with :class:`~locus_runtime.gateway.NoGrants` (fail closed).

Revocation: each grant's revocation ids are added to a persisted revoked set;
the verifier rejects any token with a revoked id (which also revokes every
attenuated token derived from it).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import biscuit_auth as biscuit

from locus_runtime.gateway import (
    ACTION_KINDS,
    Capabilities,
    GatewayAction,
    RiskClass,
)

logger = logging.getLogger(__name__)

GRANT_KEY_SECRET = "LOCUS_GRANT_AUTHORITY_KEY"
ACCEPTED_KEYS_ENV = "LOCUS_GRANT_ACCEPTED_PUBLIC_KEYS"

GrantScope = Literal["run", "standing"]
GRANT_SCOPES: tuple[str, ...] = ("run", "standing")
#: Scopes an approval of a gateway escalation may choose (``once`` = single-use ledger).
APPROVAL_SCOPES: tuple[str, ...] = ("once", "run", "standing")

STANDING_TTL = timedelta(days=30)
RUN_TTL_MAX = timedelta(hours=24)
_AUTHORIZER_MAX_TIME = timedelta(milliseconds=50)
_MAX_GRANTS_PER_LOOKUP = 256
_TOOL_KINDS = frozenset({"tool_call", "mcp_tool_call"})
_FILE_KINDS = frozenset({"file_read", "file_write"})
_TEXT_MAX = 1024


class GrantError(ValueError):
    """A grant cannot be minted, parsed or attenuated as requested."""


class GrantKeyError(GrantError):
    """The grant authority key material is missing or malformed."""


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GrantPattern:
    """The action pattern a grant covers. Every present field is a constraint."""

    kind: str
    tool: str
    target_mode: Literal["exact", "prefix"]
    target: str
    args: str | None = None
    command: str | None = None
    recipients: str | None = None
    amount_ceiling_cents: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise GrantError(f"unknown action kind {self.kind!r}")
        if not self.tool or len(self.tool) > _TEXT_MAX:
            raise GrantError("a grant pattern needs a tool")
        if self.target_mode not in ("exact", "prefix"):
            raise GrantError("target_mode must be 'exact' or 'prefix'")
        if self.target_mode == "prefix" and (not self.target or ".." in self.target):
            raise GrantError("a prefix pattern needs a concrete folder")
        if self.kind in _TOOL_KINDS and self.args is None:
            raise GrantError("a tool-call pattern must pin its arguments")
        if self.kind == "process_exec" and self.command is None:
            raise GrantError("a process_exec pattern must pin its command")
        if self.amount_ceiling_cents is not None and (
            isinstance(self.amount_ceiling_cents, bool)
            or not isinstance(self.amount_ceiling_cents, int)
            or self.amount_ceiling_cents < 0
        ):
            raise GrantError("amount_ceiling_cents must be a non-negative integer")
        for name in ("target", "args", "command", "recipients"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) > 4 * _TEXT_MAX):
                raise GrantError(f"{name} is not a bounded string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GrantPattern:
        if not isinstance(raw, Mapping):
            raise GrantError("grant pattern must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise GrantError(f"unknown grant pattern fields: {sorted(unknown)}")
        try:
            return cls(**{key: raw[key] for key in raw})
        except TypeError as exc:
            raise GrantError(str(exc)) from exc

    def describe(self) -> str:
        where = f"under {self.target}" if self.target_mode == "prefix" else f"on {self.target}"
        parts = [f"{self.kind} {self.tool} {where}"]
        if self.recipients is not None:
            parts.append(f"to {self.recipients or 'nobody'}")
        if self.amount_ceiling_cents is not None:
            parts.append(f"up to {self.amount_ceiling_cents / 100:.2f}")
        if self.command is not None:
            parts.append(f"command `{self.command}`")
        return ", ".join(parts)


def _parent_folder(path: str) -> str:
    text = str(path or "")
    cut = max(text.rfind("/"), text.rfind("\\"))
    return text[: cut + 1] if cut > 0 else ""


def tightest_pattern(action: GatewayAction) -> GrantPattern:
    """The narrowest useful pattern covering ``action`` (13 §5 grant UX).

    * file actions: the action's folder (prefix), never wider;
    * process_exec: this exact command in this workspace;
    * network_egress: this exact host;
    * tool / MCP calls: this tool on this target with these exact non-free-text
      arguments, recipients pinned to their domain set, amounts capped at the
      observed amount.

    Raises :class:`GrantError` for R4 actions: they are never grantable.
    """
    if action.risk >= RiskClass.R4:
        raise GrantError("R4 actions are never grantable")
    if action.kind not in ACTION_KINDS or not action.tool:
        raise GrantError("action is not grantable")
    facets = dict(action.facets or {})
    if action.kind in _FILE_KINDS:
        folder = _parent_folder(action.target)
        if folder and ".." not in action.target:
            return GrantPattern(action.kind, action.tool, "prefix", folder)
        return GrantPattern(action.kind, action.tool, "exact", action.target)
    if action.kind == "process_exec":
        return GrantPattern(
            action.kind,
            action.tool,
            "exact",
            action.target,
            command=str(facets.get("command", action.command_summary)),
        )
    if action.kind in {"network_egress", "model_call"}:
        return GrantPattern(action.kind, action.tool, "exact", action.target)
    amount = facets.get("amount_cents")
    return GrantPattern(
        action.kind,
        action.tool,
        "exact",
        action.target,
        args=str(facets.get("args", "{}")),
        recipients=facets.get("recipients"),
        amount_ceiling_cents=int(amount) if amount is not None else None,
    )


# --------------------------------------------------------------------------- #
# Records and storage
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GrantRecord:
    """A minted grant as stored server-side (the token never leaves the backend)."""

    grant_id: str
    principal: str
    scope: GrantScope
    pattern: Mapping[str, Any]
    token: str = field(repr=False)
    revocation_ids: tuple[str, ...]
    key_id: int
    created_at: float
    expires_at: float | None
    run_id: str = ""
    pinned: bool = False
    approver: str = ""
    parent_id: str = ""
    revoked: bool = False
    revoked_at: float | None = None
    revoked_by: str = ""

    @property
    def kind(self) -> str:
        return str(self.pattern.get("kind") or "")

    @property
    def tool(self) -> str:
        return str(self.pattern.get("tool") or "")

    def status(self, now: float) -> str:
        if self.revoked:
            return "revoked"
        if self.expires_at is not None and now > self.expires_at:
            return "expired"
        return "active"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pattern"] = dict(self.pattern)
        data["revocation_ids"] = list(self.revocation_ids)
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GrantRecord:
        scope = str(raw.get("scope") or "")
        if scope not in GRANT_SCOPES:
            raise GrantError(f"unknown grant scope {scope!r}")
        expires = raw.get("expires_at")
        revoked_at = raw.get("revoked_at")
        return cls(
            grant_id=str(raw.get("grant_id") or ""),
            principal=str(raw.get("principal") or ""),
            scope=scope,  # type: ignore[arg-type]
            pattern=dict(raw.get("pattern") or {}),
            token=str(raw.get("token") or ""),
            revocation_ids=tuple(str(item) for item in raw.get("revocation_ids") or ()),
            key_id=int(raw.get("key_id") or 0),
            created_at=float(raw.get("created_at") or 0.0),
            expires_at=float(expires) if expires is not None else None,
            run_id=str(raw.get("run_id") or ""),
            pinned=bool(raw.get("pinned")),
            approver=str(raw.get("approver") or ""),
            parent_id=str(raw.get("parent_id") or ""),
            revoked=bool(raw.get("revoked")),
            revoked_at=float(revoked_at) if revoked_at is not None else None,
            revoked_by=str(raw.get("revoked_by") or ""),
        )

    def public_view(self, now: float) -> dict[str, Any]:
        """What the API shows: everything except the bearer token itself."""
        data = self.to_dict()
        data.pop("token", None)
        data["revocation_ids"] = [item[:16] for item in self.revocation_ids]
        data["status"] = self.status(now)
        try:
            data["summary"] = GrantPattern.from_dict(self.pattern).describe()
        except GrantError:
            data["summary"] = ""
        return data


class GrantStore:
    """Server-side grants per principal plus the persisted revoked-id set.

    Holds plain JSON-able structures so the backend can persist them in its
    state store: ``records`` maps grant id → :meth:`GrantRecord.to_dict` and
    ``revoked`` lists revoked revocation ids. Pass getters to bind it to
    containers that the owner may replace (the backend re-hydrates its state).
    """

    def __init__(
        self,
        records: Callable[[], MutableMapping[str, dict[str, Any]]] | None = None,
        revoked: Callable[[], MutableSequence[str]] | None = None,
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        own_records: dict[str, dict[str, Any]] = {}
        own_revoked: list[str] = []
        self._records = records or (lambda: own_records)
        self._revoked = revoked or (lambda: own_revoked)
        self._on_change = on_change
        self._lock = threading.Lock()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def add(self, record: GrantRecord) -> GrantRecord:
        with self._lock:
            self._records()[record.grant_id] = record.to_dict()
        self._changed()
        return record

    def get(self, grant_id: str) -> GrantRecord | None:
        raw = self._records().get(str(grant_id or ""))
        if not isinstance(raw, Mapping):
            return None
        try:
            return GrantRecord.from_dict(raw)
        except (GrantError, TypeError, ValueError):
            return None

    def records(self, principal: str | None = None) -> list[GrantRecord]:
        out: list[GrantRecord] = []
        for raw in list(self._records().values()):
            if not isinstance(raw, Mapping):
                continue
            try:
                record = GrantRecord.from_dict(raw)
            except (GrantError, TypeError, ValueError):
                continue
            if principal is None or record.principal == principal:
                out.append(record)
        out.sort(key=lambda item: item.created_at)
        return out

    def active_grants(self, principal: str, now: float) -> list[GrantRecord]:
        return [
            record
            for record in self.records(principal)
            if record.status(now) == "active" and not self.is_revoked(record.revocation_ids)
        ]

    def is_revoked(self, revocation_ids: Sequence[str]) -> bool:
        revoked = set(self._revoked())
        return any(item in revoked for item in revocation_ids)

    def revoke(self, grant_id: str, *, by: str = "", now: float | None = None) -> GrantRecord:
        """Revoke a grant (idempotent) and everything attenuated from it."""
        with self._lock:
            raw = self._records().get(grant_id)
            if not isinstance(raw, Mapping):
                raise KeyError(grant_id)
            record = GrantRecord.from_dict(raw)
            revoked = self._revoked()
            for item in record.revocation_ids:
                if item not in revoked:
                    revoked.append(item)
            if not record.revoked:
                updated = dict(raw)
                updated.update(
                    revoked=True,
                    revoked_at=time.time() if now is None else now,
                    revoked_by=str(by or ""),
                )
                self._records()[grant_id] = updated
                record = GrantRecord.from_dict(updated)
        self._changed()
        return record


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #
def key_id_for(public_key: biscuit.PublicKey) -> int:
    """Stable 31-bit root key id derived from the public key."""
    digest = hashlib.sha256(bytes(public_key.to_bytes())).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _decode_key_bytes(text: str) -> bytes:
    value = str(text or "").strip()
    if len(value) == 64:
        try:
            return bytes.fromhex(value)
        except ValueError:
            pass
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise GrantKeyError("grant key is neither hex nor url-safe base64") from exc
    if len(raw) != 32:
        raise GrantKeyError("grant key must be 32 bytes")
    return raw


def generate_authority_secret() -> str:
    """A fresh private key in the secret's storage format (url-safe base64)."""
    raw = bytes(biscuit.KeyPair().private_key.to_bytes())
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class GrantAuthority:
    """Signs grants with the current key; verifies against all accepted keys."""

    def __init__(
        self,
        signing_key: biscuit.PrivateKey | None,
        accepted_public_keys: Sequence[biscuit.PublicKey] = (),
    ) -> None:
        self._keypair = (
            biscuit.KeyPair.from_private_key(signing_key) if signing_key is not None else None
        )
        accepted: dict[int, biscuit.PublicKey] = {}
        if self._keypair is not None:
            accepted[key_id_for(self._keypair.public_key)] = self._keypair.public_key
        for public in accepted_public_keys:
            accepted.setdefault(key_id_for(public), public)
        self._accepted = accepted

    @classmethod
    def generate(cls) -> GrantAuthority:
        return cls(biscuit.KeyPair().private_key)

    @classmethod
    def from_secret(cls, secret: str, accepted_public_keys: str = "") -> GrantAuthority:
        """Build from the stored private key and the operator's rotation list."""
        try:
            # The stubs shipped with biscuit-python 0.4.0 predate the ``alg``
            # argument and ``Algorithm`` members the extension itself exposes.
            private = biscuit.PrivateKey.from_bytes(  # type: ignore[call-arg]
                _decode_key_bytes(secret),
                biscuit.Algorithm.Ed25519,  # type: ignore[attr-defined]
            )
        except GrantKeyError:
            raise
        except Exception as exc:  # noqa: BLE001 - library rejects malformed key bytes
            raise GrantKeyError("grant key is not a valid Ed25519 private key") from exc
        publics: list[biscuit.PublicKey] = []
        for item in str(accepted_public_keys or "").split(","):
            item = item.strip()
            if not item:
                continue
            try:
                publics.append(
                    biscuit.PublicKey.from_bytes(  # type: ignore[call-arg]
                        _decode_key_bytes(item),
                        biscuit.Algorithm.Ed25519,  # type: ignore[attr-defined]
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a bad rotation entry is a config error
                raise GrantKeyError("an accepted public key is malformed") from exc
        return cls(private, publics)

    @property
    def can_mint(self) -> bool:
        return self._keypair is not None

    @property
    def key_id(self) -> int:
        if self._keypair is None:
            raise GrantKeyError("no signing key")
        return key_id_for(self._keypair.public_key)

    @property
    def public_key_hex(self) -> str:
        if self._keypair is None:
            raise GrantKeyError("no signing key")
        return bytes(self._keypair.public_key.to_bytes()).hex()

    @property
    def accepted_key_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._accepted))

    def _root(self, key_id: int | None) -> biscuit.PublicKey:
        if key_id is None or key_id not in self._accepted:
            raise GrantError("token was not signed by an accepted grant key")
        return self._accepted[key_id]

    def parse(self, token: str) -> biscuit.Biscuit:
        """Verify the signature chain against the accepted key named by the token."""
        try:
            return biscuit.Biscuit.from_base64(str(token or ""), self._root)
        except Exception as exc:  # noqa: BLE001 - every parse/verify failure is a reject
            raise GrantError("grant token failed verification") from exc

    def mint(
        self,
        pattern: GrantPattern,
        *,
        principal: str,
        scope: GrantScope,
        run_id: str = "",
        pinned: bool = False,
        ttl: timedelta | None = None,
        approver: str = "",
        now: float | None = None,
    ) -> GrantRecord:
        """Mint a grant. Run grants are bound to the run and live at most 24h;
        standing grants expire after 30 days unless pinned."""
        if self._keypair is None:
            raise GrantKeyError("this authority cannot mint (no signing key)")
        principal = str(principal or "").strip()
        if not principal:
            raise GrantError("a grant needs a principal")
        if scope not in GRANT_SCOPES:
            raise GrantError(f"unknown grant scope {scope!r}")
        if scope == "run" and not str(run_id or "").strip():
            raise GrantError("a run grant needs a run id")
        if scope == "run" and pinned:
            raise GrantError("only standing grants can be pinned")
        issued = time.time() if now is None else now
        if scope == "run":
            lifetime = min(ttl or RUN_TTL_MAX, RUN_TTL_MAX)
        else:
            lifetime = ttl or STANDING_TTL
        if lifetime <= timedelta(0):
            raise GrantError("grant lifetime must be positive")
        expires_at = None if pinned else issued + lifetime.total_seconds()
        grant_id = f"grant-{uuid4()}"

        params: dict[str, Any] = {
            "gid": grant_id,
            "principal": principal,
            "scope": scope,
            "kind": pattern.kind,
            "tool": pattern.tool,
            "target": pattern.target,
        }
        code = [
            "grant_id({gid}); grant_principal({principal}); grant_scope({scope});",
            "grant_kind({kind}); grant_tool({tool});",
            "check if principal({principal});",
            "check if action({kind}, {tool});",
        ]
        if pattern.target_mode == "exact":
            code.append("check if action_target({target});")
        else:
            code.append("check if action_target($t), $t.starts_with({target});")
            code.append('reject if action_target($t), $t.contains("..");')
        if pattern.args is not None:
            params["args"] = pattern.args
            code.append("check if action_args({args});")
        if pattern.command is not None:
            params["command"] = pattern.command
            code.append("check if action_command({command});")
        if pattern.recipients is not None:
            params["recipients"] = pattern.recipients
            code.append("check if recipients({recipients});")
        else:
            code.append("reject if recipients($r);")
        if pattern.amount_ceiling_cents is not None:
            params["ceiling"] = int(pattern.amount_ceiling_cents)
            code.append("check if amount($a), $a <= {ceiling};")
        else:
            code.append("reject if amount($a);")
        if scope == "run":
            params["run"] = str(run_id)
            code.append("grant_run({run}); check if run({run});")
        if pinned:
            code.append("grant_pinned(true);")
        if expires_at is not None:
            params["exp"] = datetime.fromtimestamp(expires_at, tz=UTC)
            code.append("check if time($t), $t <= {exp};")
        builder = biscuit.BiscuitBuilder("\n".join(code), params)
        builder.set_root_key_id(self.key_id)
        token = builder.build(self._keypair.private_key)
        return GrantRecord(
            grant_id=grant_id,
            principal=principal,
            scope=scope,
            pattern=pattern.to_dict(),
            token=token.to_base64(),
            revocation_ids=tuple(token.revocation_ids),
            key_id=self.key_id,
            created_at=issued,
            expires_at=expires_at,
            run_id=str(run_id or "") if scope == "run" else "",
            pinned=bool(pinned),
            approver=str(approver or ""),
        )


def attenuate(
    authority: GrantAuthority,
    record: GrantRecord,
    *,
    run_id: str | None = None,
    target_prefix: str | None = None,
    expires_at: float | None = None,
    amount_ceiling_cents: int | None = None,
) -> GrantRecord:
    """Derive a narrower grant for a sub-run by appending a checks-only block.

    The appended block can only add conditions; it cannot widen what the
    authority block allows (see the module docstring). The derived record keeps
    the parent's revocation ids, so revoking the parent revokes it too.
    """
    parent = authority.parse(record.token)
    checks: list[str] = []
    params: dict[str, Any] = {}
    if run_id is not None:
        if not str(run_id).strip():
            raise GrantError("run_id must be non-empty")
        params["run"] = str(run_id)
        checks.append("check if run({run});")
    if target_prefix is not None:
        if not target_prefix or ".." in target_prefix:
            raise GrantError("target_prefix must be a concrete folder")
        params["prefix"] = target_prefix
        checks.append("check if action_target($t), $t.starts_with({prefix});")
    if expires_at is not None:
        params["exp"] = datetime.fromtimestamp(expires_at, tz=UTC)
        checks.append("check if time($t), $t <= {exp};")
    if amount_ceiling_cents is not None:
        if amount_ceiling_cents < 0:
            raise GrantError("amount_ceiling_cents must be non-negative")
        params["ceiling"] = int(amount_ceiling_cents)
        checks.append("check if amount($a), $a <= {ceiling};")
    if not checks:
        raise GrantError("attenuation must add at least one restriction")
    child = parent.append(biscuit.BlockBuilder("\n".join(checks), params))
    new_expiry = record.expires_at
    if expires_at is not None:
        new_expiry = expires_at if new_expiry is None else min(new_expiry, expires_at)
    return GrantRecord(
        grant_id=f"grant-{uuid4()}",
        principal=record.principal,
        scope="run" if run_id is not None else record.scope,
        pattern=dict(record.pattern),
        token=child.to_base64(),
        revocation_ids=tuple(child.revocation_ids),
        key_id=record.key_id,
        created_at=time.time(),
        expires_at=new_expiry,
        run_id=str(run_id) if run_id is not None else record.run_id,
        pinned=record.pinned and expires_at is None,
        approver=record.approver,
        parent_id=record.grant_id,
    )


# --------------------------------------------------------------------------- #
# Verification at the gateway
# --------------------------------------------------------------------------- #
def _authorizer_code(action: GatewayAction, now: float) -> tuple[str, dict[str, Any]]:
    facets = dict(action.facets or {})
    params: dict[str, Any] = {
        "principal": action.caller.principal,
        "run": action.caller.run_id,
        "kind": action.kind,
        "tool": action.tool,
        "target": action.target,
        "now": datetime.fromtimestamp(now, tz=UTC),
    }
    code = [
        "principal({principal}); run({run}); action({kind}, {tool}); action_target({target});",
        "time({now});",
    ]
    if "args" in facets:
        params["args"] = str(facets["args"])
        code.append("action_args({args});")
    if action.kind == "process_exec":
        params["command"] = str(facets.get("command", action.command_summary))
        code.append("action_command({command});")
    if "recipients" in facets:
        params["recipients"] = str(facets["recipients"])
        code.append("recipients({recipients});")
    if "amount_cents" in facets:
        params["amount"] = int(facets["amount_cents"])
        code.append("amount({amount});")
    code.append("allow if grant_id($g);")
    return "\n".join(code), params


class BiscuitGrantVerifier:
    """:class:`~locus_runtime.gateway.GrantVerifier` backed by Biscuit grants.

    Looks up the authenticated principal's grants in the server-side
    :class:`GrantStore`, verifies each token's signature (accepted keys by root
    key id), rejects revoked ones and runs the Datalog authorizer against facts
    built from the gateway's own action. Any error means "not covered".
    """

    def __init__(
        self,
        authority: GrantAuthority,
        store: GrantStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._authority = authority
        self._store = store
        self._clock = clock

    @property
    def authority(self) -> GrantAuthority:
        return self._authority

    @property
    def store(self) -> GrantStore:
        return self._store

    @property
    def ready(self) -> bool:
        """Keys are loaded: there is at least one accepted verification key."""
        return bool(self._authority.accepted_key_ids)

    @property
    def can_mint(self) -> bool:
        return self._authority.can_mint

    def token_covers(self, token: str, action: GatewayAction) -> bool:
        """Verify one token against ``action`` (signature, revocation, Datalog)."""
        try:
            parsed = self._authority.parse(token)
        except GrantError:
            return False
        if self._store.is_revoked(list(parsed.revocation_ids)):
            return False
        code, params = _authorizer_code(action, self._clock())
        try:
            builder = biscuit.AuthorizerBuilder(code, params)
            # Default limit is 1 ms; allow headroom on a loaded host (a timeout still
            # fails closed). limits()/set_limits() are missing from the 0.4.0 stubs.
            limits = builder.limits()  # type: ignore[attr-defined]
            limits.max_time = _AUTHORIZER_MAX_TIME
            builder.set_limits(limits)  # type: ignore[attr-defined]
            builder.build(parsed).authorize()
        except Exception:  # noqa: BLE001 - AuthorizationError or any datalog failure
            return False
        return True

    def match(self, action: GatewayAction, capabilities: Capabilities) -> GrantRecord | None:  # noqa: ARG002
        principal = action.caller.principal
        if not principal or not action.caller.run_id or action.risk >= RiskClass.R4:
            return None
        now = self._clock()
        candidates = [
            record
            for record in self._store.active_grants(principal, now)
            if record.kind == action.kind
            and record.tool == action.tool
            and (record.scope != "run" or record.run_id == action.caller.run_id)
        ][:_MAX_GRANTS_PER_LOOKUP]
        for record in candidates:
            if self.token_covers(record.token, action):
                return record
        return None

    def covers(self, action: GatewayAction, capabilities: Capabilities) -> bool:
        return self.match(action, capabilities) is not None


# --------------------------------------------------------------------------- #
# Key resolution (fail closed)
# --------------------------------------------------------------------------- #
def load_grant_authority(
    resolve: Callable[[str], str | None] | None = None,
) -> GrantAuthority | None:
    """The grant authority from the secure secret store, or ``None``.

    Resolution is read-only (env → OS keychain → Windows DPAPI, per LOCUS-315);
    the native launcher provisions the key. ``None`` -- no key, no secure store,
    or malformed key material -- means the gateway runs without grants.
    """
    if resolve is None:
        try:
            from locus_tooling.native_secrets import get_secret
        except Exception:  # noqa: BLE001 - no secret resolver means no authority
            logger.warning("grants.no_secret_resolver")
            return None

        def resolve(name: str) -> str | None:
            return get_secret(name)

    try:
        secret = resolve(GRANT_KEY_SECRET)
    except Exception as exc:  # noqa: BLE001 - SecretStorageUnavailable and friends
        logger.warning("grants.key_unavailable: %s", type(exc).__name__)
        return None
    if not secret:
        logger.info("grants.no_authority_key")
        return None
    try:
        return GrantAuthority.from_secret(secret, os.getenv(ACCEPTED_KEYS_ENV, ""))
    except GrantKeyError as exc:
        logger.warning("grants.key_invalid: %s", exc)
        return None
