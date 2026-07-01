"""Client-side gRPC interceptor: attach the auth token to every outgoing call.

Interceptors are the gRPC equivalent of middleware — the right place for
cross-cutting concerns so the call sites stay focused on business logic. This one
adds the shared auth token as call metadata on every unary RPC; the inventory
server's auth interceptor (decision D-034) checks the same header and rejects with
``UNAUTHENTICATED`` if it is missing or wrong.

The metadata key ``AUTH_METADATA_KEY`` must stay in sync with the server side.
gRPC metadata keys are lower-cased by convention; using an ``x-`` prefix keeps it
clearly a custom, non-reserved header.
"""
from collections.abc import Callable

from grpc.aio import ClientCallDetails, UnaryUnaryClientInterceptor

# Shared with inventory-service's server-side auth interceptor (D-034).
AUTH_METADATA_KEY = "x-auth-token"


class AuthClientInterceptor(UnaryUnaryClientInterceptor):
    """Attach the auth token as metadata on every outgoing unary-unary RPC."""

    def __init__(self, token: str) -> None:
        """Store the token to attach to each call.

        Args:
            token: The shared secret the inventory server expects. May be empty
                in local dev, in which case the server (with an unset token) skips
                enforcement — see the server interceptor.
        """
        self._token = token

    async def intercept_unary_unary(
        self,
        continuation: Callable,
        client_call_details: ClientCallDetails,
        request: object,
    ) -> object:
        """Inject the auth header, then continue the call.

        Args:
            continuation: Callable that resumes the RPC with the (possibly
                modified) call details.
            client_call_details: Original call details (method, metadata, ...).
            request: The outgoing request message.

        Returns:
            The RPC call object returned by ``continuation``.
        """
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
