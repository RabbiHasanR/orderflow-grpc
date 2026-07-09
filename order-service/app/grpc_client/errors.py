"""Map gRPC status codes to HTTP status codes at the REST edge.

Shared by every module that fronts an inventory RPC so the translation stays in
one place (DRY): a transport/status failure from inventory becomes a sensible
HTTP status instead of a 500.
"""
import grpc
from fastapi import status

# gRPC status → HTTP status; anything unlisted falls back to 502 Bad Gateway.
_GRPC_TO_HTTP = {
    grpc.StatusCode.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED: status.HTTP_504_GATEWAY_TIMEOUT,
    grpc.StatusCode.UNAUTHENTICATED: status.HTTP_502_BAD_GATEWAY,
}


def grpc_to_http_status(code: grpc.StatusCode) -> int:
    """Map a gRPC status code to an HTTP status (default 502 Bad Gateway)."""
    return _GRPC_TO_HTTP.get(code, status.HTTP_502_BAD_GATEWAY)
