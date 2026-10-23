# LOCUS-470/B: no implicit mutable base image; build requires an exact digest.
# Example: --build-arg NODE_BASE=node@sha256:<verified registry manifest>
ARG NODE_BASE
FROM ${NODE_BASE}
ARG CODEX_CLI_VERSION=0.163.0
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --no-install-recommends -y python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global --no-audit --no-fund "@openai/codex@${CODEX_CLI_VERSION}" \
    && test "$(codex --version)" = "codex-cli ${CODEX_CLI_VERSION}"
COPY docker/codex/stdio_runner.py /usr/local/bin/locus-codex-stdio
RUN chmod 0555 /usr/local/bin/locus-codex-stdio \
    && python3 -m py_compile /usr/local/bin/locus-codex-stdio
# Never mount the Docker socket, host profile, Git config or provider credentials.
# StageDockerBroker always overrides the entrypoint and host UID/GID.
# Other standalone Docker invocations must also default to nonroot.
USER node
ENTRYPOINT ["/usr/local/bin/locus-codex-stdio"]
