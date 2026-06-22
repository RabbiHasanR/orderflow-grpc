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

## Tone
Learning project — explain the gRPC / distributed-systems concept behind a change,
not just the code.
