# syntax=docker/dockerfile:1.7
#
# order-service — FastAPI REST edge + gRPC client (async, decision D-030).
# Multi-stage, production-grade image (mirrors inventory-service, D-027):
#   * builder stage installs deps into an isolated venv (cached on requirements)
#   * runtime stage copies only that venv + app code → no pip cache, no toolchain
#   * runs as a non-root user, ships no secrets, drains gracefully on SIGTERM
#
# Build context is ./order-service. The generated/ stubs are produced out-of-band
# by scripts/regen_proto.sh and COPYed in; we never ship grpcio-tools.

# Pin the interpreter so rebuilds are reproducible. slim-bookworm + manylinux
# wheels (asyncpg, grpcio) means no compiler/libpq-dev is required.
ARG PYTHON_VERSION=3.12-slim-bookworm

# ----------------------------------------------------------------------------
# Stage 1: builder — resolve and install runtime deps into a relocatable venv.
# ----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

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
    PATH="/opt/venv/bin:$PATH"

# Unprivileged user — never run the server as root.
RUN groupadd --system app && useradd --system --gid app --no-create-home --home /app app

WORKDIR /app

# Bring over the prebuilt venv from the builder (no pip, no build tooling).
COPY --from=builder /opt/venv /opt/venv

# Application code (incl. app/generated/ stubs). .dockerignore keeps caches,
# venvs and tooling out of the context.
COPY --chown=app:app . .

# Entrypoint exec's the ASGI server (exec → PID-1 signals → graceful lifespan).
RUN chmod +x docker-entrypoint.sh

USER app

EXPOSE 8000

# HTTP liveness probe against the health endpoint (order-service speaks HTTP).
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"]

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
