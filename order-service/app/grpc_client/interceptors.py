"""Client-side gRPC interceptor: attach the auth token to every outgoing call.

The inventory server's auth interceptor (D-034) verifies the same header and
rejects with ``UNAUTHENTICATED`` if it is missing or wrong.
"""
from collections.abc import Callable

from grpc.aio import ClientCallDetails, UnaryUnaryClientInterceptor

# Must stay in sync with inventory-service's server-side auth interceptor (D-034).
AUTH_METADATA_KEY = "x-auth-token"


class AuthClientInterceptor(UnaryUnaryClientInterceptor):
    """Attach the auth token as metadata on every outgoing unary-unary RPC."""

    def __init__(self, token: str) -> None:
        """Store the token to attach to each call (may be empty in local dev)."""
        self._token = token

    async def intercept_unary_unary(
        self,
        continuation: Callable,
        client_call_details: ClientCallDetails,
        request: object,
    ) -> object:
        """Inject the auth header, then continue the call."""
        metadata = list(client_call_details.metadata or [])
        metadata.append((AUTH_METADATA_KEY, self._token))

        new_details = ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(new_details, request)
