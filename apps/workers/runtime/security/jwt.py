from __future__ import annotations
import os
import time
from typing import Any, Dict, Optional

from locus_runtime.security import RuntimeTokenIdentity, token_identity_from_claims

try:
    import jwt  # type: ignore
except Exception:  # pragma: no cover - pyjwt optional at scaffolding time
    jwt = None  # type: ignore


class JWTConfig:
    def __init__(self) -> None:
        self.algorithm = os.getenv("A2A_JWT_ALG", "HS256")
        self.issuer = os.getenv("A2A_JWT_ISS", "lattix-locus")
        self.audience = os.getenv("A2A_JWT_AUD", "locus-runtime")
        self.secret = os.getenv("A2A_JWT_SECRET")  # for HS256
        self.private_key = os.getenv("A2A_JWT_PRIVATE_KEY")
        self.public_key = os.getenv("A2A_JWT_PUBLIC_KEY")


def issue_token(
    sub: str,
    ttl_seconds: int = 600,
    cfg: Optional[JWTConfig] = None,
    additional_claims: Optional[Dict[str, Any]] = None,
) -> str:
    if jwt is None:
        raise RuntimeError("pyjwt not installed; cannot issue tokens")
    cfg = cfg or JWTConfig()
    now = int(time.time())
    payload: Dict[str, Any] = {
        "iss": cfg.issuer,
        "aud": cfg.audience,
        "sub": sub,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if additional_claims:
        payload.update(additional_claims)
    if cfg.algorithm.startswith("HS"):
        if not cfg.secret:
            raise RuntimeError("A2A_JWT_SECRET is required for HS* algorithms")
        return jwt.encode(payload, cfg.secret, algorithm=cfg.algorithm)
    else:
        if not cfg.private_key:
            raise RuntimeError("A2A_JWT_PRIVATE_KEY is required for asymmetric algorithms")
        return jwt.encode(payload, cfg.private_key, algorithm=cfg.algorithm)


def verify_token(token: str, cfg: Optional[JWTConfig] = None) -> Dict[str, Any]:
    if jwt is None:
        raise RuntimeError("pyjwt not installed; cannot verify tokens")
    cfg = cfg or JWTConfig()
    key: Optional[str]
    if cfg.algorithm.startswith("HS"):
        key = cfg.secret
        if not key:
            raise RuntimeError("A2A_JWT_SECRET is required for HS* verification")
    else:
        key = cfg.public_key
        if not key:
            raise RuntimeError("A2A_JWT_PUBLIC_KEY is required for asymmetric verification")
    return jwt.decode(
        token, key, algorithms=[cfg.algorithm], audience=cfg.audience, issuer=cfg.issuer
    )


def extract_identity(token: str, cfg: Optional[JWTConfig] = None) -> RuntimeTokenIdentity:
    return token_identity_from_claims(verify_token(token, cfg))
