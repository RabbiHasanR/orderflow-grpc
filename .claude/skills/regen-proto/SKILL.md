---
name: regen-proto
description: Regenerate gRPC Python stubs from proto/*.proto into both order-service and inventory-service generated/ dirs. Use after editing any .proto file.
---

# Regenerate proto stubs

Run:

    bash scripts/regen_proto.sh

This generates `*_pb2.py`, `*_pb2_grpc.py`, and `*.pyi` into BOTH:

- `order-service/app/generated/`
- `inventory-service/generated/`

## Rules
- Never hand-edit generated output — change `proto/*.proto` and rerun this.
- Requires `grpcio-tools` installed in the active environment.
- A PostToolUse hook runs this automatically when a `proto/*.proto` file changes;
  run it manually if you regenerate outside an edit (e.g. after a fresh clone).
