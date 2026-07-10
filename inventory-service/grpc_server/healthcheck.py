"""Container health probe: query the gRPC health service, exit 0 iff SERVING.

Run as ``python -m grpc_server.healthcheck`` from the Dockerfile HEALTHCHECK. It
talks to the local server's ``grpc.health.v1.Health`` endpoint, which is
auth-exempt (see AuthInterceptor), so the probe needs no token. This replaces the
old bare TCP-connect check with a real readiness signal: a server that is
draining (NOT_SERVING) or wedged fails the probe even though the port is open.
"""
import os
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def main() -> int:
    """Return 0 if the local server reports SERVING, else 1."""
    port = os.environ.get("GRPC_PORT", "50051")
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            response = stub.Check(
                health_pb2.HealthCheckRequest(service=""), timeout=3
            )
    except grpc.RpcError as exc:
        print(f"health check failed: {exc.code()}", file=sys.stderr)
        return 1

    if response.status == health_pb2.HealthCheckResponse.SERVING:
        return 0
    print(f"not serving: {response.status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
