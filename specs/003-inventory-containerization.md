---
id: 003
title: Inventory-service containerization
service: inventory-service
status: done
created: 2026-06-30
updated: 2026-06-30
related: [architecture.md, "architecture.md#3-container--compose-topology", "decisions.md#d-027", "inventory-service/Dockerfile", docker-compose.yml]
---

## Context / Why

inventory-service has working code (models, service layer, servicer, gRPC server)
but no way to run it as a container. This spec delivers a production-grade image
for inventory-service plus a Compose slice (`inventory-db` + `inventory-service`)
so the gRPC server can actually be brought up. order-service and the 2nd replica
(round-robin, architecture goal #3) are explicitly out of scope here.

## Requirements

- The system SHALL build a single inventory-service image runnable as a container.
- The image SHALL run the standalone gRPC server (`python -m grpc_server.server`),
  NOT a Django web server.
- The container SHALL apply DB migrations (incl. the 0002 product seed) before
  serving, and SHALL forward SIGTERM to the server for graceful drain.
- The image SHALL run as a non-root user and SHALL NOT bake in any secret.
- The image SHALL NOT ship dev/regeneration tooling (`grpcio-tools`/protoc).
- Compose SHALL provide `inventory-db` (Postgres, persistent named volume) and
  start inventory-service only once the DB is healthy.
- The gRPC port SHALL NOT be published to the host (internal-only per architecture).
- All config/secrets SHALL come from env vars / `.env` (none hardcoded).

## Design

- **Multi-stage Dockerfile** ([Dockerfile](../inventory-service/Dockerfile)):
  builder installs runtime deps into `/opt/venv`; runtime stage copies only the
  venv + app code. `psycopg[binary]` + `grpcio` use manylinux wheels, so the
  image needs no compiler/`libpq-dev`. Base `python:3.12-slim-bookworm`, pinned
  via `ARG PYTHON_VERSION`.
- **Requirements split:** `requirements.txt` = runtime (Django, psycopg, grpcio,
  protobuf); new `requirements-dev.txt` adds `grpcio-tools` for stub regen only.
  The image installs runtime-only — no protobuf compiler in production.
- **Stubs shipped, not built in-image:** build context is `./inventory-service`
  and `proto/` lives at repo root (outside context), so `generated/` is produced
  by `scripts/regen_proto.sh` out-of-band and COPYed in. `.dockerignore` keeps
  caches/venvs/secrets/`requirements-dev.txt` out but KEEPS `generated/*_pb2*`.
- **Entrypoint** ([docker-entrypoint.sh](../inventory-service/docker-entrypoint.sh)):
  `migrate --noinput` then `exec "$@"` (the CMD), so the server is PID 1's
  successor and the existing SIGTERM drain in `server.py` works. Migrate-in-
  entrypoint is safe only at one replica (D-026); a 2nd replica needs a one-shot
  migrate job.
- **Healthcheck:** the server registers no gRPC health service, so the probe is a
  Python TCP `connect` to `GRPC_PORT` — no extra binaries, proves the listener.
- **Compose** ([docker-compose.yml](../docker-compose.yml)): `inventory-db`
  (`postgres:16-alpine`, named volume, `pg_isready` healthcheck) and
  `inventory-service` (`depends_on: condition: service_healthy`, `env_file: .env`,
  no `ports:`). `POSTGRES_HOST=inventory-db` set in compose to match the service.

## Tasks

- [done] runtime/dev requirements split
- [done] multi-stage non-root Dockerfile + `.dockerignore`
- [done] migrate-then-serve entrypoint with SIGTERM passthrough
- [done] TCP healthcheck (no gRPC health service yet)
- [done] `docker-compose.yml`: inventory-db + inventory-service (single replica)
- [todo] `.env.example` documenting required vars — blocked by the agent deny rule
  on `.env*`; user creates it manually
- [todo] 2nd replica + one-shot migrate job for round-robin (later phase, D-026)

## Changelog
- 2026-06-30 — created; containerized inventory-service (Dockerfile, entrypoint,
  compose slice) per [D-027](../decisions.md#d-027).
