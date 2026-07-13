---
id: 014
title: HAProxy per-request load-balancing edge (mock real deployment)
service: order-service
status: in-progress
created: 2026-07-13
updated: 2026-07-13
related: [architecture.md, "docker-compose.yml", "haproxy/haproxy.cfg", "009-production-multireplica-hardening.md", "decisions.md#d-043", "decisions.md#d-044"]
---

## Context / Why

The current public edge is **nginx** (D-043). To keep picking up `--scale`d
order-service replicas without a static `upstream`, nginx resolves the service
name via Docker DNS with the host in a variable and re-resolves only per the
resolver's `valid` TTL (~5s). Consequence: balancing is at **DNS-refresh
granularity**, not per request — a burst inside one 5s window can land entirely
on one replica, and nginx runs **no app-level health check**, so a sick/booting
replica can still receive traffic.

This spec replaces that edge with **HAProxy** to mock a real deployment LB:
a live, health-checked backend pool with **true per-request** round-robin.
Scope is the REST edge only (client → order-service); gRPC balancing toward
inventory-service is unchanged (client-side round-robin, D-026).

## Requirements

1. Single host-published edge on `:8000`, same stable URL as before.
2. **Per-request** round-robin across all current order-service replicas.
3. **Active health check** per replica (`GET /healthz`); drain unhealthy slots.
4. Pick up `--scale order-service=N` at runtime with no restart (N ≤ slot count).
5. Preserve streaming: NDJSON `/inventory/low-stock` and WS `/inventory/stock-watch`
   (no response buffering cut-off; long-lived / upgraded connections survive).
6. Edge self-liveness endpoint for the container healthcheck, independent of backends.
7. order-service stays internal (no host port); master's nginx path is untouched.

## Design

- New `haproxy/haproxy.cfg`, `haproxy:3.0-alpine` service replacing the `nginx`
  service in `docker-compose.yml` (same `8000:80` publish, same `depends_on`).
- **Discovery:** `resolvers docker` → `127.0.0.11`; `server-template order 4
  order-service:8000 check resolvers docker` pre-provisions 4 slots continuously
  re-resolved from the service record set. `hold valid 5s` bounds discovery only.
- **Balancing:** `balance roundrobin` in `mode http` → per-request across the
  known-live pool (instant), independent of the 5s discovery TTL.
- **Health:** `option httpchk GET /healthz` + `http-check expect status 200`,
  `inter 5s` — a failed check drains the slot from rotation.
- **Streaming:** `timeout client/server/tunnel 1h`; HTTP mode auto-handles the
  WebSocket `Upgrade`; `tunnel` governs the post-upgrade connection.
- **Edge liveness:** `/haproxy-health` returned directly by HAProxy.
- Decision recorded as **D-044** (supersedes D-043 on this branch).

## Tasks

- [done] Author `haproxy/haproxy.cfg` (resolvers, server-template, health, timeouts).
- [done] Swap `nginx` → `haproxy` service in `docker-compose.yml`; fix stale comments.
- [done] Validate config (`haproxy -c`).
- [done] Add decision **D-044**; add this spec + README index row.
- [ ] End-to-end verify: `--scale order-service=2`, confirm requests hit both
      replicas per-request and a killed replica is drained within one check.

## Changelog

- 2026-07-13: Created; HAProxy edge implemented on branch `feat/haproxy-edge`
  (replaces nginx D-043); runtime `--scale` verification pending.
