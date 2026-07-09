"""Server-side gRPC interceptors: auth-token enforcement and per-RPC logging."""
import logging
import time
from collections.abc import Callable

import grpc
from django.conf import settings

logger = logging.getLogger("inventory.grpc")

# Shared with the order-service client interceptor (D-034).
AUTH_METADATA_KEY = "x-auth-token"


def _abort_handler(
    handler: grpc.RpcMethodHandler, code: grpc.StatusCode, details: str
) -> grpc.RpcMethodHandler:
    """Build an aborting handler of the *same RPC kind* as ``handler``.

    The replacement must match the real handler's kind (unary-unary,
    stream-unary, or unary-stream) or gRPC mis-dispatches it, so we branch on
    ``handler``. ``context.abort`` raises, so the same ``terminate`` works for a
    streaming handler too — it aborts before yielding anything.
    """

    def terminate(request: object, context: grpc.ServicerContext) -> None:
        context.abort(code, details)

    if handler.stream_unary:
        return grpc.stream_unary_rpc_method_handler(terminate)
    if handler.unary_stream:
        return grpc.unary_stream_rpc_method_handler(terminate)
    return grpc.unary_unary_rpc_method_handler(terminate)


class AuthInterceptor(grpc.ServerInterceptor):
    """Reject RPCs that do not carry the expected auth token.

    If ``GRPC_AUTH_TOKEN`` is unset (local dev), enforcement is skipped.
    """

    def __init__(self) -> None:
        self._expected = settings.GRPC_AUTH_TOKEN

    def intercept_service(self, continuation, handler_call_details):
        """Check the token metadata; pass through or return an aborting handler."""
        if not self._expected:
            return continuation(handler_call_details)  # dev: no token configured

        metadata = dict(handler_call_details.invocation_metadata or [])
        presented = metadata.get(AUTH_METADATA_KEY)
        handler = continuation(handler_call_details)
        if presented != self._expected:
            logger.warning(
                "rejecting %s: missing/invalid auth token", handler_call_details.method
            )
            return _abort_handler(
                handler, grpc.StatusCode.UNAUTHENTICATED, "invalid auth token"
            )

        return handler


class LoggingInterceptor(grpc.ServerInterceptor):
    """Log method, peer, resulting status and latency for every RPC."""

    def intercept_service(self, continuation, handler_call_details):
        """Wrap the resolved handler to time the call and log its outcome."""
        handler = continuation(handler_call_details)
        if handler is None:
            return handler

        method = handler_call_details.method

        def timed(inner: Callable) -> Callable:
            """Wrap a single-response handler to time it and log the outcome."""

            def wrapper(request_or_iter: object, context: grpc.ServicerContext) -> object:
                start = time.perf_counter()
                try:
                    response = inner(request_or_iter, context)
                except Exception:
                    # Covers context.abort() (raises) and unexpected errors.
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    logger.info(
                        "%s peer=%s status=%s %.1fms",
                        method, context.peer(), context.code(), elapsed_ms,
                    )
                    raise
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info("%s peer=%s status=OK %.1fms", method, context.peer(), elapsed_ms)
                return response

            return wrapper

        def timed_stream(inner: Callable) -> Callable:
            """Wrap a response-streaming handler.

            Unlike ``timed``, a server-streaming handler returns *lazily*: calling
            it just builds a generator, so latency and message count are only
            known once the stream is fully drained. We therefore iterate it here,
            counting messages, and log on completion or on error.
            """

            def wrapper(request: object, context: grpc.ServicerContext) -> object:
                start = time.perf_counter()
                count = 0
                try:
                    for response in inner(request, context):
                        count += 1
                        yield response
                except Exception:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    logger.info(
                        "%s peer=%s status=%s msgs=%d %.1fms",
                        method, context.peer(), context.code(), count, elapsed_ms,
                    )
                    raise
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    "%s peer=%s status=OK msgs=%d %.1fms",
                    method, context.peer(), count, elapsed_ms,
                )

            return wrapper

        # Handle the RPC kinds this service exposes: unary-unary (ReserveStock),
        # stream-unary (ReserveStockBulk), and unary-stream (WatchLowStock).
        # Other kinds pass through unwrapped.
        if handler.unary_unary:
            return grpc.unary_unary_rpc_method_handler(
                timed(handler.unary_unary),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if handler.stream_unary:
            return grpc.stream_unary_rpc_method_handler(
                timed(handler.stream_unary),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if handler.unary_stream:
            return grpc.unary_stream_rpc_method_handler(
                timed_stream(handler.unary_stream),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        return handler
