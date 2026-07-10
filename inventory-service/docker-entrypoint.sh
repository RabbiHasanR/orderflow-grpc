#!/usr/bin/env bash
# Container entrypoint for inventory-service.
#
# Pure `exec` handoff: the given command becomes PID 1's successor and receives
# SIGTERM directly — that's what triggers the graceful drain in grpc_server/server.py.
#
# Migrations are no longer run here. With 2+ replicas, running `migrate` in every
# container would race on the shared inventory-db (D-026). Migrations now run once
# in the dedicated `inventory-migrate` one-shot compose service, which the app
# waits on via `service_completed_successfully`. This same entrypoint also backs
# that migrate service — compose just overrides `command` to the migrate call.
set -euo pipefail

echo "[entrypoint] starting: $*"
exec "$@"
