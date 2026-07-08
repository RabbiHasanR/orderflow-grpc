"""Client-side gRPC interceptors: attach the auth token to every outgoing call.

The inventory server's auth interceptor (D-034) verifies the same header and
rejects with ``UNAUTHENTICATED`` if it is missing or wrong.

grpc.aio gotcha: a channel partitions its interceptors by RPC *kind* with an
elif-chain, so a single object that inherits several ``*ClientInterceptor``
mixins lands in only the FIRST matching bucket. One interceptor instance
therefore serves exactly one RPC kind — we register one per kind we use
(unary-unary for ``ReserveStock``, stream-unary for ``ReserveStockBulk``), both
built by :func:`auth_client_interceptors`.
"""
from collections.abc import Callable

from grpc.aio import (
    ClientCallDetails,
    ClientInterceptor,
    StreamUnaryClientInterceptor,
    UnaryUnaryClientInterceptor,
)

# Must stay in sync with inventory-service's server-side auth interceptor (D-034).
AUTH_METADATA_KEY = "x-auth-token"


class _AuthBase:
    """Shared token store + header injection for the per-kind interceptors."""

    def __init__(self, token: str) -> None:
        """Store the token to attach to each call (may be empty in local dev)."""
        self._token = token

    def _with_auth(self, details: ClientCallDetails) -> ClientCallDetails:
        """Return a copy of ``details`` with the auth header appended."""
        metadata = list(details.metadata or [])
        metadata.append((AUTH_METADATA_KEY, self._token))
        return ClientCallDetails(
            method=details.method,
            timeout=details.timeout,
            metadata=metadata,
            credentials=details.credentials,
            wait_for_ready=details.wait_for_ready,
        )


class UnaryUnaryAuthInterceptor(_AuthBase, UnaryUnaryClientInterceptor):
    """Attach the auth token on unary-unary RPCs (``ReserveStock``)."""

    async def intercept_unary_unary(
        self,
        continuation: Callable,
        client_call_details: ClientCallDetails,
        request: object,
    ) -> object:
        """Inject the auth header, then continue the unary-unary call."""
        return await continuation(self._with_auth(client_call_details), request)


class StreamUnaryAuthInterceptor(_AuthBase, StreamUnaryClientInterceptor):
    """Attach the auth token on stream-unary RPCs (``ReserveStockBulk``)."""

    async def intercept_stream_unary(
        self,
        continuation: Callable,
        client_call_details: ClientCallDetails,
        request_iterator: object,
    ) -> object:
        """Inject the auth header, then continue the stream-unary call."""
        return await continuation(self._with_auth(client_call_details), request_iterator)


def auth_client_interceptors(token: str) -> list[ClientInterceptor]:
    """One auth interceptor per RPC kind the client uses (see module docstring)."""
    return [UnaryUnaryAuthInterceptor(token), StreamUnaryAuthInterceptor(token)]
