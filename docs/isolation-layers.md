# Agent Isolation Layers & Multi-Agent Workflow Security

## Concentric Isolation Layers

```mermaid
block-beta
  columns 1

  block:L0["🌐 LAYER 0 — Network Boundary"]
    columns 1
    L0a["Caddy Reverse Proxy · CORS · Security Headers (HSTS, CSP, X-Frame-Options)"]

    block:L1["🔐 LAYER 1 — Authentication & Access Control"]
      columns 1
      L1a["OIDC (Casdoor) · 4-Tier Route ACL: public-minimal → authenticated-read → authenticated-mutate → internal-only"]

      block:L2["📜 LAYER 2 — Policy Engine (OPA)"]
        columns 1
        L2a["agent_policy.rego · filesystem_access.rego · tool_jail.rego · Network Egress Allowlist · Budget Enforcement"]

        block:L3["🔗 LAYER 3 — A2A Transport Security"]
          columns 1
          L3a["JWT Bearer (HS256) · HMAC-SHA256 Signed Headers · Nonce Replay Protection · Trusted Subject Allowlist"]

          block:L4["📡 LAYER 4 — Event Bus & Middleware"]
            columns 1
            L4a["Topic Pub/Sub · Tenant Isolation · Memory Scope AuthZ · Time + Token Budget Enforcement"]

            block:L5["📦 LAYER 5 — Hybrid Sandbox (Three-Tier)"]
              columns 1
              L5a["Tier 1: Kernel (bwrap/seatbelt) · Tier 2: Hardened Docker (seccomp + read-only + resource limits) · Tier 3: K8s (gVisor/Kata RuntimeClass)"]

              block:L6["🔒 LAYER 6 — Tool Jail & Egress Control"]
                columns 1
                L6a["Custom seccomp BPF (40+ blocked syscalls) · Squid domain allowlist · Time-limited capability tokens · DLP filter · Guardrails chain"]
              end
            end
          end
        end
      end
    end
  end

  style L0 fill:#1a1a2e,color:#e0e0ff,stroke:#4a4a8a
  style L1 fill:#16213e,color:#e0e0ff,stroke:#4a7a8a
  style L2 fill:#0f3460,color:#e0e0ff,stroke:#4a8a8a
  style L3 fill:#1a4060,color:#e0e0ff,stroke:#4a8aaa
  style L4 fill:#1a5276,color:#e0e0ff,stroke:#5a9aba
  style L5 fill:#1f6f8b,color:#e0e0ff,stroke:#6abacc
  style L6 fill:#2a9d8f,color:#1a1a2e,stroke:#52d4c0
```

## Multi-Agent Workflow Architecture

```mermaid
flowchart TB
    subgraph External["🌐 Network Boundary"]
        Client["Browser Client"]
        Caddy["Caddy Gateway\n:80"]
    end

    subgraph Backend["🔐 Backend (FastAPI :8000)"]
        Auth["OIDC Auth\nMiddleware"]
        RouteACL["Route Access\nControl"]
        OPA["OPA Policy\nEngine :8181"]
    end

    subgraph Orchestration["📡 Layer 1 — Orchestrator"]
        Orch["Orchestrator\n(Stage Runner)"]
        EB["Event Bus\n(Topic Pub/Sub)"]
        SecMW["Security\nMiddleware"]
    end

    subgraph Agents["📦 Agent Containers"]
        subgraph A1["agent-research :8081"]
            A1E["Envelope\nEndpoint"]
            A1JWT["JWT + A2A\nVerification"]
            A1H["Research\nHandler"]
        end
        subgraph A2["agent-code :8082"]
            A2E["Envelope\nEndpoint"]
            A2JWT["JWT + A2A\nVerification"]
            A2H["Code\nHandler"]
        end
        subgraph A3["agent-review :8083"]
            A3E["Envelope\nEndpoint"]
            A3JWT["JWT + A2A\nVerification"]
            A3H["Review\nHandler"]
        end
    end

    subgraph Sandbox["🔒 Tool Jail"]
        GR["Guardrails\nFilter Chain"]
        SB["Sandbox\nContainer"]
        Squid["Squid Egress\nProxy :3128"]
    end

    subgraph Data["💾 Data Services"]
        Vault["HashiCorp\nVault"]
        NATS["NATS\nMessaging"]
        Redis["Redis\nCache"]
        PG["PostgreSQL"]
    end

    Client -->|HTTPS| Caddy
    Caddy -->|/api/*| Auth
    Auth --> RouteACL
    RouteACL -->|Policy Check| OPA

    RouteACL -->|Signed A2A| Orch
    Orch --> EB
    EB --> SecMW
    SecMW -->|Tenant Isolation\nMemory AuthZ\nBudget Check| EB

    EB -->|"Signed Envelope\n(JWT + HMAC + Nonce)"| A1E
    EB -->|"Signed Envelope\n(JWT + HMAC + Nonce)"| A2E
    EB -->|"Signed Envelope\n(JWT + HMAC + Nonce)"| A3E

    A1E --> A1JWT --> A1H
    A2E --> A2JWT --> A2H
    A3E --> A3JWT --> A3H

    A1H -->|Tool Exec| GR
    A2H -->|Tool Exec| GR
    A3H -->|Tool Exec| GR

    GR -->|"seccomp-strict.json\ncap-drop=ALL\nno-new-privileges\nreadonly rootfs\nnetwork=none"| SB
    SB -->|Mediated Egress| Squid

    A1H -.->|Secrets| Vault
    A2H -.->|Secrets| Vault
    Orch -.-> NATS
    Orch -.-> Redis
    Orch -.-> PG

    style External fill:#1a1a2e,color:#e0e0ff,stroke:#4a4a8a
    style Backend fill:#16213e,color:#e0e0ff,stroke:#4a7a8a
    style Orchestration fill:#0f3460,color:#e0e0ff,stroke:#4a8a8a
    style Agents fill:#1a5276,color:#e0e0ff,stroke:#5a9aba
    style A1 fill:#1f6f8b,color:#e0e0ff,stroke:#6abacc
    style A2 fill:#1f6f8b,color:#e0e0ff,stroke:#6abacc
    style A3 fill:#1f6f8b,color:#e0e0ff,stroke:#6abacc
    style Sandbox fill:#2a9d8f,color:#1a1a2e,stroke:#52d4c0
    style Data fill:#264653,color:#e0e0ff,stroke:#4a8a8a
```

## Agent-to-Agent Envelope Security

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant EB as Event Bus
    participant SM as Security Middleware
    participant A1 as Agent-Research
    participant A2 as Agent-Code
    participant OPA as OPA Policy Engine

    O->>EB: publish(topic, envelope)
    Note over O,EB: Envelope contains:<br/>correlation_id, actor,<br/>tenant_id, session_id,<br/>time_limit_ms, cost_limit_tokens

    EB->>SM: pre-delivery middleware
    SM->>SM: Resolve auth context
    SM->>SM: Validate tenant isolation
    SM->>SM: Check memory scope authorization
    SM->>SM: Enforce budget limits

    alt Security Blocked
        SM-->>EB: security_blocked = true
        EB-->>O: Delivery rejected + security_events
    else Allowed
        SM-->>EB: Envelope cleared

        EB->>A1: POST /v1/envelope
        Note over EB,A1: Headers:<br/>Authorization: Bearer {JWT}<br/>X-Locus-Subject: orchestrator<br/>X-Locus-Nonce: {uuid}<br/>X-Locus-Signature: HMAC-SHA256<br/>X-Correlation-ID: {trace}

        A1->>A1: Verify JWT (iss, aud, exp, sub)
        A1->>A1: Verify HMAC signature
        A1->>A1: Check nonce replay cache
        A1->>A1: Validate trusted subject

        A1->>OPA: Evaluate tool policy
        OPA-->>A1: allow/deny + constraints

        A1->>A1: Execute with guardrails
        A1-->>EB: Result envelope

        Note over A1,A2: Agent-to-Agent via Event Bus<br/>(no direct connections)

        EB->>A2: POST /v1/envelope
        Note over EB,A2: Same signed transport
        A2-->>EB: Result envelope
        EB-->>O: Aggregated results
    end
```

## Runtime Profile Comparison

```mermaid
graph LR
    subgraph LW["local-lightweight"]
        LW1["Minimal auth"]
        LW2["Localhost binding"]
        LW3["No strict headers"]
        LW4["Dev convenience"]
    end

    subgraph LS["local-secure"]
        LS1["Full auth stack"]
        LS2["A2A signed transport"]
        LS3["OPA policy enforcement"]
        LS4["Hardened Docker sandbox"]
        LS5["Custom seccomp profile"]
        LS6["Squid domain allowlist"]
        LS7["Tenant isolation"]
    end

    subgraph H["hosted"]
        H1["HTTPS-only A2A"]
        H2["TLS cert verification"]
        H3["Strict replay protection"]
        H4["No localhost exceptions"]
        H5["Full identity enforcement"]
        H6["gVisor/Kata RuntimeClass"]
        H7["K8s seccomp + network policies"]
    end

    LW -->|"Hardened"| LS
    LS -->|"Production"| H

    style LW fill:#f4a261,color:#1a1a2e,stroke:#e76f51
    style LS fill:#2a9d8f,color:#1a1a2e,stroke:#264653
    style H fill:#264653,color:#e0e0ff,stroke:#2a9d8f
```

## Memory Scope Authorization Matrix

```mermaid
graph TD
    subgraph Scopes["Memory Scope Hierarchy"]
        G["🌍 global\n(internal_service only)"]
        R["🔄 run\n(internal_service only)"]
        W["⚙️ workflow\n(internal_service OR authenticated actor)"]
        AG["🤖 agent\n(internal_service OR actor with membership)"]
        T["🏢 tenant\n(matching tenant claim required)"]
        U["👤 user\n(authenticated actor)"]
        S["📋 session\n(session_id OR actor match)"]
    end

    G --> R
    R --> W
    W --> AG
    AG --> T
    T --> U
    U --> S

    style G fill:#e76f51,color:#fff,stroke:#c44536
    style R fill:#f4a261,color:#1a1a2e,stroke:#e76f51
    style W fill:#e9c46a,color:#1a1a2e,stroke:#f4a261
    style AG fill:#2a9d8f,color:#fff,stroke:#264653
    style T fill:#264653,color:#e0e0ff,stroke:#2a9d8f
    style U fill:#1a5276,color:#e0e0ff,stroke:#264653
    style S fill:#0f3460,color:#e0e0ff,stroke:#1a5276
```
