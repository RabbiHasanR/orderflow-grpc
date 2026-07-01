"""Server-side gRPC interceptors for the inventory-service.

Interceptors are gRPC's middleware — the place for cross-cutting concerns so the
servicer stays focused on business logic (architecture.md §6). Two are wired here:

* :class:`AuthInterceptor` — rejects calls whose auth-token metadata is missing or
  wrong (``UNAUTHENTICATED``). The order-service client attaches the token via its
  own interceptor; the shared metadata key is ``x-auth-token`` (decision D-034).
* :class:`LoggingInterceptor` — records method, peer, status and latency per RPC.

Both are **synchronous** ``grpc.ServerInterceptor``s because inventory runs a sync
``grpcio`` server (decision D-021), not ``grpc.aio``.
"""
import logging
import time

import grpc
from django.conf import settings

logger = logging.getLogger("inventory.grpc")

# Shared with the order-service client interceptor (D-034). gRPC lower-cases
# metadata keys; the ``x-`` prefix marks it a custom, non-reserved header.
AUTH_METADATA_KEY = "x-auth-token"


def _abort_handler(code: grpc.StatusCode, details: str) -> grpc.RpcMethodHandler:
    """Build a unary-unary handler that immediately aborts every call.

    Used to short-circuit a rejected request without ever reaching the servicer.
    """

    def terminate(request: object, context: grpc.ServicerContext) -> None:
        context.abort(code, details)

    return grpc.unary_unary_rpc_method_handler(terminate)


class AuthInterceptor(grpc.ServerInterceptor):
    """Reject RPCs that do not carry the expected auth token.

    If ``GRPC_AUTH_TOKEN`` is unset on the server (local dev), enforcement is
    skipped — mirroring the settings' dev fallbacks so the stack runs without a
    token locally while staying secure once one is configured.
    """

    def __init__(self) -> None:
        self._expected = settings.GRPC_AUTH_TOKEN

    def intercept_service(self, continuation, handler_call_details):
        """Check the token metadata; pass through or return an aborting handler."""
        if not self._expected:
            return continuation(handler_call_details)  # dev: no token configured

        metadata = dict(handler_call_details.invocation_metadata or [])
        presented = metadata.get(AUTH_METADATA_KEY)
        if presented != self._expected:
            logger.warning(
                "rejecting %s: missing/invalid auth token", handler_call_details.method
            )
            return _abort_handler(grpc.StatusCode.UNAUTHENTICATED, "invalid auth token")

        return continuation(handler_call_details)


class LoggingInterceptor(grpc.ServerInterceptor):
    """Log method, peer, resulting status and latency for every RPC."""

    def intercept_service(self, continuation, handler_call_details):
        """Wrap the resolved handler to time the call and log its outcome."""
        handler = continuation(handler_call_details)
        # Only unary-unary RPCs exist today; leave other kinds untouched.
        if handler is None or not handler.unary_unary:
            return handler

        method = handler_call_details.method

        def wrapper(request: object, context: grpc.ServicerContext) -> object:
            start = time.perf_counter()
            try:
                response = handler.unary_unary(request, context)
            except Exception:
                # Covers context.abort() (raises) and unexpected servicer errors.
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    "%s peer=%s status=%s %.1fms",
                    method, context.peer(), context.code(), elapsed_ms,
                )
                raise
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info("%s peer=%s status=OK %.1fms", method, context.peer(), elapsed_ms)
            return response

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
