# syntax=docker/dockerfile:1.7
#
# inventory-service — Django-as-ORM + standalone gRPC server.
# Multi-stage, production-grade image:
#   * builder stage installs deps into an isolated venv (cached on requirements)
#   * runtime stage copies only that venv + app code → no pip cache, no toolchain
#   * runs as a non-root user, ships no secrets, drains gracefully on SIGTERM
#
# Build context is ./inventory-service. The generated/ stubs are produced
# out-of-band by scripts/regen_proto.sh and COPYed in; we never ship grpcio-tools.

# Pin the interpreter so rebuilds are reproducible. slim-bookworm + manylinux
# wheels (psycopg[binary], grpcio) means no compiler/libpq-dev is required.
ARG PYTHON_VERSION=3.12-slim-bookworm

# ----------------------------------------------------------------------------
# Stage 1: builder — resolve and install runtime deps into a relocatable venv.
# ----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

# Build-time hygiene only (no .pyc, unbuffered logs, no pip version chatter).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Self-contained venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy ONLY requirements first so this layer is cached until deps change.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# ----------------------------------------------------------------------------
# Stage 2: runtime — minimal image with just the venv + app code.
# ----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=inventory_project.settings

# Unprivileged user — never run the server as root.
RUN groupadd --system app && useradd --system --gid app --no-create-home --home /app app

WORKDIR /app

# Bring over the prebuilt venv from the builder (no pip, no build tooling).
COPY --from=builder /opt/venv /opt/venv

# Application code (incl. generated/ stubs and migrations). .dockerignore keeps
# caches, venvs and tooling out of the context.
COPY --chown=app:app . .

# Entrypoint runs migrations then exec's the gRPC server (exec → PID-1 signals).
RUN chmod +x docker-entrypoint.sh

USER app

EXPOSE 50051

# Real readiness probe: query the grpc.health.v1 service (auth-exempt) and pass
# only when it reports SERVING. A draining or wedged server fails this even with
# the port open — the signal the round-robin client needs to route around it.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "grpc_server.healthcheck"]

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "grpc_server.server"]
