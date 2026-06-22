#!/usr/bin/env bash
# Regenerate gRPC Python stubs from proto/*.proto into BOTH services.
# Single source of truth for the regeneration command across both language setups.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_DIR="$ROOT/proto"

TARGETS=(
  "$ROOT/order-service/app/generated"
  "$ROOT/inventory-service/generated"
)

if ! ls "$PROTO_DIR"/*.proto >/dev/null 2>&1; then
  echo "No .proto files found in $PROTO_DIR — nothing to generate." >&2
  exit 0
fi

for OUT in "${TARGETS[@]}"; do
  mkdir -p "$OUT"
  touch "$OUT/__init__.py"

  python -m grpc_tools.protoc \
    -I "$PROTO_DIR" \
    --python_out="$OUT" \
    --grpc_python_out="$OUT" \
    --pyi_out="$OUT" \
    "$PROTO_DIR"/*.proto

  # protoc emits `import foo_pb2 as ...` in *_pb2_grpc.py, which breaks when the
  # stubs live inside a package. Rewrite to a relative import.
  for f in "$OUT"/*_pb2_grpc.py; do
    [ -e "$f" ] || continue
    sed -i -E 's/^import (.+_pb2) as/from . import \1 as/' "$f"
  done
done

echo "Regenerated stubs into: ${TARGETS[*]}"
