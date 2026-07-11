---
id: 011
title: Security & config hardening for production
service: both
status: draft
created: 2026-07-11
updated: 2026-07-11
related: [architecture.md, "009-production-multireplica-hardening.md", "decisions.md#d-034", "decisions.md#d-043", "inventory-service/grpc_server/interceptors.py", "docker-compose.yml"]
---

## Context / Why

This is **Phase 5** of the multi-replica production plan. The stack has several
insecure-by-default fallbacks that are fine for local dev but must be locked down
before production. Called out during the plan's exploration.

## Requirements

- **Fail-closed auth in prod:** today the gRPC auth check is skipped when
  `GRPC_AUTH_TOKEN` is empty (dev convenience,
  [interceptors.py](../inventory-service/grpc_server/interceptors.py) / D-034). A
  prod flag SHALL make a missing token a startup error, not silent open access.
- **Django prod defaults:** `DJANGO_SECRET_KEY` SHALL be required (no
  `dev-insecure-change-me` fallback) and `ALLOWED_HOSTS` SHALL be set to real hosts
  when not in debug.
- **`.env.example`:** a tracked template SHALL exist (compose references it but it is
  not in git today), documenting every required variable.
- **Resource limits:** compose services SHALL declare CPU/memory limits so one
  replica can't starve the host.
- **TLS:** terminate TLS at **nginx** (the single edge added in D-043) for external
  traffic. Internal gRPC/HTTP stays plaintext on the private network unless mTLS is
  later justified (still a documented non-goal for now).
- **Process model:** decide order-service worker model — one uvicorn loop per
  replica (scale via replicas, current) vs Gunicorn+UvicornWorker for multiple
  workers per container. Document the choice.

## Design

- Auth: add a settings flag (e.g. `GRPC_REQUIRE_AUTH`/env) checked at server startup;
  keep the empty-token dev path only when the flag is off.
- Django: change `settings.py` to `os.environ["DJANGO_SECRET_KEY"]` (required) and
  derive `ALLOWED_HOSTS` from an env var; keep permissive values only under
  `DJANGO_DEBUG`.
- `.env.example`: mirror the keys compose interpolates (`*_POSTGRES_*`,
  `GRPC_AUTH_TOKEN`, `INVENTORY_GRPC_TARGETS`, `DJANGO_SECRET_KEY`, timeouts).
- Compose: add `deploy.resources.limits` (or `mem_limit`/`cpus`) per service.
- TLS: nginx `server` block on 443 with certs mounted; redirect 80→443. Certs via a
  mounted volume or a future ACME/Traefik path.

## Tasks

- [todo] fail-closed gRPC auth behind a prod flag
- [todo] require `DJANGO_SECRET_KEY`; real `ALLOWED_HOSTS`
- [todo] tracked `.env.example`
- [todo] compose resource limits (CPU/memory) per service
- [todo] TLS termination at nginx (443 + redirect)
- [todo] decide + document order-service worker model (gunicorn optional)

## Changelog
- 2026-07-11 — drafted from the approved multi-replica plan (Phase 5). TLS is now
  scoped to nginx termination since D-043 made nginx the single edge.
