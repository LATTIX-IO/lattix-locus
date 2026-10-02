FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY frontier_tooling ./frontier_tooling
COPY frontier_runtime ./frontier_runtime
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/backend

# git is required at runtime: the multi-agent build/implementer nodes provision
# git worktrees and produce unified diffs via the harness coding loop.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 lattix \
    && useradd --uid 1000 --gid 1000 --create-home lattix

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . /app

USER lattix
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -f http://localhost:8000/healthz || exit 1

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
