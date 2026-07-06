#!/usr/bin/env bash
# Container entrypoint for order-service.
#
# Schema is managed by Alembic (supersedes D-033's startup create_all). We run
# `alembic upgrade head` here — the same "migrate on deploy" step inventory-service
# performs with Django `migrate` — so the schema is reconciled before the server
# accepts traffic. Then `exec` hands off to the ASGI server so it becomes PID 1's
# successor and receives SIGTERM directly for a clean shutdown (closes the gRPC
# channel + DB engine in the lifespan's finally block).
set -euo pipefail

echo "[entrypoint] running migrations: alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
