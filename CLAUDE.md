# OrderFlow gRPC — Project Instructions

Learning project for cross-framework gRPC. Full design in architecture.md.
Global preferences (~/.claude/CLAUDE.md) still apply.

## Architecture (quick ref)
- order-service: FastAPI · gRPC client · REST :8000 · SQLAlchemy · own Postgres (order-db)
- inventory-service: Django · gRPC server · :50051 internal · 2 replicas · own Postgres (inventory-db)
- proto/order_inventory.proto = source-of-truth contract, owned by neither service.

## Hard rules
- NEVER hand-edit */generated/ — it is protoc output. Edit the .proto, then regenerate.
- After editing proto/*.proto, regenerate stubs into BOTH services (see the regen-proto skill).
- gRPC backends are internal-only; only order-service:8000 is host-published.
- Secrets via env vars only (auth token: GRPC_AUTH_TOKEN).

## Commands
- Regenerate stubs:  bash scripts/regen_proto.sh
- Run stack:         docker compose up --build
- Watch round-robin: docker compose logs -f order-service inventory-service-1 inventory-service-2

## Spec-driven workflow

Specs in `specs/` are the source of truth for WHAT to build and WHY. One
capability = one file (`specs/NNN-kebab-name.md`). `architecture.md` /
`decisions.md` / `workflow.md` remain the system-wide big picture; specs link to
them, never duplicate them. Format is defined once in the `spec` skill.

- START of any non-trivial task: read `specs/README.md` (cheap index), then open
  only the matching spec. Prefer the spec over re-scanning the whole repo
  (token discipline). If no spec matches, this is new work.
- Maintain specs AUTOMATICALLY — the user does not ask:
  - New capability with no spec → create `specs/NNN-name.md` (next free ID from
    the index comment) and add a row to `specs/README.md`, before/with coding.
  - Plan or scope changes → update that spec's Requirements/Design/Tasks, bump
    `updated:`, add a `## Changelog` line.
  - Work completes → flip Tasks to `[done]`, set `status`, update the index row.
- Significant design choices still get a `decisions.md` `D-NNN` entry; the spec
  links to it.

## Tone
Learning project — explain the gRPC / distributed-systems concept behind a change,
not just the code.
