#!/usr/bin/env bash
# Container entrypoint for order-service.
#
# Pure `exec` handoff: the ASGI server becomes PID 1's successor and receives
# SIGTERM directly for a clean shutdown (closes the gRPC channel + DB engine in
# the lifespan's finally block).
#
# Schema is managed by Alembic (supersedes D-033's startup create_all), but
# `alembic upgrade head` no longer runs here — with 2+ replicas every container
# would race the upgrade on the shared order-db. It now runs once in the dedicated
# `order-migrate` one-shot compose service, which the app waits on via
# `service_completed_successfully`. That migrate service reuses this same
# entrypoint with `command` overridden to the alembic call.
set -euo pipefail

echo "[entrypoint] starting: $*"
exec "$@"
