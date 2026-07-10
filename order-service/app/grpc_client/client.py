"""gRPC client to inventory-service: channel, stub, and a typed helper.

Uses ``grpc.aio`` (D-030) with client-side ``round_robin`` load balancing over a
``dns:///`` target, so scaling inventory replicas needs no code change.
"""
import json
import logging
from collections.abc import AsyncIterator

import grpc
from grpc.aio import Channel

from app.core.config import Settings
from app.grpc_client.interceptors import auth_client_interceptors
from app.generated import order_inventory_pb2 as pb2
from app.generated import order_inventory_pb2_grpc as pb2_grpc

logger = logging.getLogger("order.grpc")

_SERVICE = "orderflow.inventory.v1.InventoryService"

# Channel service config:
#  * round_robin — balance across every replica IP dns:/// resolves (not pick_first).
#  * retryPolicy — transparently retry ReserveStock/ReleaseStock on UNAVAILABLE
#    (a replica cycling), so a blip during --scale/rollout doesn't reach the user.
#    Safe ONLY because these calls are idempotent (idempotency_key / release is a
#    no-op replay). The streaming + business-reject paths are deliberately excluded.
_SERVICE_CONFIG = json.dumps(
    {
        "loadBalancingConfig": [{"round_robin": {}}],
        "methodConfig": [
            {
                "name": [
                    {"service": _SERVICE, "method": "ReserveStock"},
                    {"service": _SERVICE, "method": "ReleaseStock"},
                ],
                "retryPolicy": {
                    "maxAttempts": 4,
                    "initialBackoff": "0.1s",
                    "maxBackoff": "1s",
                    "backoffMultiplier": 2,
                    "retryableStatusCodes": ["UNAVAILABLE"],
                },
            }
        ],
    }
)


def build_channel(settings: Settings) -> Channel:
    """Create the round-robin ``grpc.aio`` channel with the auth interceptor."""
    # dns:/// so the resolver returns all replica IPs and re-resolves over time.
    target = f"dns:///{settings.inventory_targets[0]}"
    options = [
        ("grpc.service_config", _SERVICE_CONFIG),
        ("grpc.enable_retries", 1),
        # Keepalive so a long-lived, idle WatchStock stream isn't silently dropped
        # by a NAT/LB idle timeout: ping every 30s even with no in-flight calls.
        # The server permits pings this frequent (see grpc_server/server.py).
        ("grpc.keepalive_time_ms", 30000),
        ("grpc.keepalive_timeout_ms", 10000),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.http2.max_pings_without_data", 0),
    ]

    logger.info("inventory gRPC channel → %s (round_robin, retries, keepalive)", target)
    return grpc.aio.insecure_channel(  # internal network; TLS is a future extension
        target,
        options=options,
        interceptors=auth_client_interceptors(settings.grpc_auth_token),
    )


class InventoryClient:
    """Thin async wrapper around the generated ``InventoryServiceStub``."""

    def __init__(self, channel: Channel, deadline_seconds: float) -> None:
        """Bind the stub to a channel and remember the per-call deadline."""
        self._stub = pb2_grpc.InventoryServiceStub(channel)
        self._deadline = deadline_seconds

    async def reserve_stock(
        self, order_ref: str, items: list[tuple[int, int]], idempotency_key: str = ""
    ) -> pb2.ReserveStockResponse:
        """Call ``ReserveStock`` for one order.

        ``idempotency_key`` makes a retry safe: inventory replays the original
        outcome instead of reserving twice. Raises ``grpc.aio.AioRpcError`` on
        transport/status failure (mapped to HTTP by the caller).
        """
        request = pb2.ReserveStockRequest(
            order_ref=order_ref,
            items=[pb2.ReserveItem(product_id=pid, quantity=qty) for pid, qty in items],
            idempotency_key=idempotency_key,
        )
        return await self._stub.ReserveStock(request, timeout=self._deadline)

    async def release_stock(self, order_ref: str) -> pb2.ReleaseStockResponse:
        """Call ``ReleaseStock`` to return a reserved order's stock (compensation).

        Used when the order fails to persist after a successful reserve, so held
        stock is not stranded. Idempotent server-side. Raises
        ``grpc.aio.AioRpcError`` on transport/status failure.
        """
        request = pb2.ReleaseStockRequest(order_ref=order_ref)
        return await self._stub.ReleaseStock(request, timeout=self._deadline)

    async def reserve_stock_bulk(
        self, lines: list[tuple[str, int, int]]
    ) -> pb2.BulkReserveSummary:
        """Stream reservation lines across many orders; return the one summary.

        Client-streaming: instead of a single request message, ``grpc.aio`` wants
        an *async iterator* of messages. We hand it ``_request_gen``; grpc pulls
        one ``BulkReserveItem`` at a time and sends it, and the single awaited
        result is the server's end-of-stream ``BulkReserveSummary``.

        Args:
            lines: ``(order_ref, product_id, quantity)`` tuples, possibly spanning
                many orders.

        Raises:
            grpc.aio.AioRpcError: on transport/status failure (mapped to HTTP by
                the caller).
        """

        async def _request_gen():
            for order_ref, pid, qty in lines:
                yield pb2.BulkReserveItem(
                    order_ref=order_ref, product_id=pid, quantity=qty
                )

        return await self._stub.ReserveStockBulk(
            _request_gen(), timeout=self._deadline
        )

    async def watch_low_stock(
        self, threshold: int, sku_prefix: str = ""
    ) -> AsyncIterator[pb2.ProductStock]:
        """Stream products at/below ``threshold``; yield each as it arrives.

        Server-streaming: the stub call returns an async iterator (not an
        awaitable). We ``async for`` over it and re-yield, so the caller consumes
        one ``ProductStock`` at a time and nothing is buffered — the constant-
        memory property holds all the way from the DB cursor to here.

        The per-call ``timeout`` bounds the *whole* stream, which suits this
        bounded query; a true long-lived "watch" would drop or extend it.

        Args:
            threshold: Inclusive upper bound on ``available_quantity``.
            sku_prefix: Optional SKU prefix filter; "" means no filter.

        Raises:
            grpc.aio.AioRpcError: on transport/status failure. It surfaces when
                iteration starts, so the caller can map a pre-stream failure to an
                HTTP status before the response body begins (see the router).
        """
        request = pb2.LowStockQuery(threshold=threshold, sku_prefix=sku_prefix)
        async for product in self._stub.WatchLowStock(request, timeout=self._deadline):
            yield product

    async def watch_stock(
        self, commands: AsyncIterator[pb2.WatchCommand]
    ) -> AsyncIterator[pb2.StockUpdate]:
        """Open the bidirectional ``WatchStock`` call: commands in, updates out.

        Bidi: we hand the stub an *async iterator* of ``WatchCommand`` (driven by
        whatever produces commands — e.g. a WebSocket) and it returns an async
        iterator of ``StockUpdate``. Both directions run concurrently over one
        call; we just re-yield each update as it arrives.

        Note the deliberate absence of ``timeout=self._deadline``: a live watch is
        **unbounded**, so the per-call deadline the other methods use would kill it
        after a few seconds. Teardown is client-driven — when ``commands`` is
        exhausted (the caller closes it) or the call is cancelled, the stream ends.
        This is the "a true long-lived watch would drop or extend the deadline"
        caveat from D-037, made concrete.

        Args:
            commands: Async iterator of ``WatchCommand`` (subscribe/unsubscribe).

        Raises:
            grpc.aio.AioRpcError: on transport/status failure. It surfaces when
                iteration starts, so the caller can distinguish a pre-stream
                failure from a mid-stream one (see the WebSocket bridge).
        """
        async for update in self._stub.WatchStock(commands):
            yield update
