#!/usr/bin/env bash
# Container entrypoint for inventory-service.
#
# Applies database migrations (incl. the 0002 product seed), then hands off to
# the gRPC server via `exec` so the server becomes PID 1's successor and receives
# SIGTERM directly — that's what triggers the graceful drain in grpc_server/server.py.
#
# Running migrate here is safe while we run a SINGLE replica (decision D-026). When
# the 2nd replica is added for round-robin, migrations must move to a one-shot
# job to avoid two replicas racing `migrate` on the shared inventory-db.
set -euo pipefail

echo "[entrypoint] applying migrations..."
python manage.py migrate --noinput

echo "[entrypoint] starting: $*"
exec "$@"
