#!/usr/bin/env bash
# Container entrypoint for order-service.
#
# Unlike inventory-service (which runs Django `migrate` here), order-service
# creates its tables from the app itself at startup via SQLAlchemy
# `Base.metadata.create_all` in the FastAPI lifespan (decision D-033, no Alembic
# for v1). So this entrypoint only hands off to the ASGI server via `exec` — the
# server becomes PID 1's successor and receives SIGTERM directly for a clean
# shutdown (closes the gRPC channel + DB engine in the lifespan's finally block).
set -euo pipefail

echo "[entrypoint] starting: $*"
exec "$@"
