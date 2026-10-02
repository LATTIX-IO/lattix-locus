Agent Service Template

Purpose
- Containerized microservice wrapper for a single agent. Exposes HTTP APIs for A2A/MCP and subscribes to topics via the runtime bus if running in-process.

Endpoints
- POST /v1/envelope — accept an Envelope JSON and process
- GET /healthz — minimal public liveness check
- GET /healthz/details — authenticated health details
- GET /readyz — readiness check (authenticated in strict profiles)

Security
- Authorization: Bearer JWT (A2A). See `runtime/security/jwt.py` for issuer/verification.
- Hosted/service-separated deployments should set `LOCUS_RUNTIME_PROFILE=hosted` and keep `LOCUS_REQUIRE_A2A_RUNTIME_HEADERS=true` so worker services follow the same signed runtime-header contract as the canonical backend.
- Preferred identity claims: `actor`, `tenant_id`, `subject`, and `internal_service` so worker services and the canonical backend can interpret the same caller identity semantics.
- In `local-secure` and `hosted` profiles, worker service envelope and detailed readiness/health surfaces require an authenticated bearer token with `internal_service=true`.
- In strict profiles, runtime callers must also send `X-Locus-Subject`, `X-Locus-Nonce`, `X-Locus-Signature`, and `X-Correlation-ID`; worker services verify the HMAC signature and reject nonce replay.
- The shared runtime JWT audience is `locus-runtime`; examples and manifests should keep worker services on that audience unless a deliberate compatibility override is documented.
- In the `hosted` runtime profile, A2A clients should target `https://` endpoints only; plaintext `http://` transport is reserved for explicitly local profiles.
- mTLS (optional): terminate at ingress; forward identity via headers and enforce JWT.

Run locally
- `pip install -r requirements.txt`
- `uvicorn app:app --host 0.0.0.0 --port 8080`

Container
- `docker build -t agent-service:dev .`
- `docker run -p 8080:8080 agent-service:dev`

Kubernetes
- See `k8s/deployment.yaml` and `k8s/service.yaml` (adjust image and env).

