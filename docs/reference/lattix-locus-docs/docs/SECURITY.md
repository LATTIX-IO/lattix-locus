Service-to-Service (A2A) and MCP Security

> Historical reference note: this document captures security guidance imported from an earlier docs tree. For the active repository security baseline and trust-boundary expectations, prefer `THREAT-MODEL.md` and `docs/SECURITY.md`.

Goals
- Authenticate and authorize calls between agent services.
- Support autoscaling and identity per service.
- Keep protocol simple: HTTP+JSON for Envelopes; optional broker for events.

A2A (HTTP) — JWT
- Use signed JWT Bearer tokens for A2A requests.
- Issuer/audience: configured via env (A2A_JWT_ISS, A2A_JWT_AUD).
- Shared runtime default audience is `locus-runtime`; older worker-only `agents` values should be treated as legacy compatibility inputs and normalized forward.
- Algorithms: HS256 (dev) or RS256/ES256 (prod) using KMS-managed keys.
- Code: `runtime/security/jwt.py` (issue/verify), `runtime/network/a2a.py` (client).
- Preferred identity claims across worker and backend boundaries: `actor`, `tenant_id`, `subject`, and `internal_service`.
- Propagate correlation: include `correlation_id` in the Envelope body; consider also an `X-Correlation-ID` header.

mTLS (optional)
- Terminate TLS at ingress or mesh (e.g., Istio/Linkerd). Client certs provide service identity.
- Enforce JWT at the application even with mTLS for defense-in-depth.

MCP (Model Context Protocol)
- Treat MCP connections like A2A with JWT or mTLS for auth.
- Prefer mutually authenticated channels or signed session tokens.
- Route MCP traffic via a gateway that validates identities and scopes.
 - Rotate/expire MCP session tokens frequently; scope access to the minimum tools.

Secrets & Key Management
- Store secrets/keys in a secret manager or Kubernetes Secrets.
- Rotate keys regularly; use short-lived tokens for A2A.

Least Privilege
- Scope tokens per service and per action; restrict endpoints to necessary subjects.

Audit
- Include correlation_id in logs; persist structured logs (Envelope payload.logs).
