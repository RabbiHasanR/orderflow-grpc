"""gRPC client to inventory-service: channel, stub, and a typed helper.

Uses ``grpc.aio`` (D-030) with client-side ``round_robin`` load balancing over a
``dns:///`` target, so scaling inventory replicas needs no code change.
"""
import logging
from collections.abc import AsyncIterator

import grpc
from grpc.aio import Channel

from app.core.config import Settings
from app.grpc_client.interceptors import auth_client_interceptors
from app.generated import order_inventory_pb2 as pb2
from app.generated import order_inventory_pb2_grpc as pb2_grpc

logger = logging.getLogger("order.grpc")

# Load-balance across all resolved addresses instead of the default pick_first.
_ROUND_ROBIN_SERVICE_CONFIG = '{"loadBalancingConfig":[{"round_robin":{}}]}'


def build_channel(settings: Settings) -> Channel:
    """Create the round-robin ``grpc.aio`` channel with the auth interceptor."""
    # dns:/// so the resolver returns all replica IPs and re-resolves over time.
    target = f"dns:///{settings.inventory_targets[0]}"
    options = [("grpc.service_config", _ROUND_ROBIN_SERVICE_CONFIG)]

    logger.info("inventory gRPC channel → %s (round_robin)", target)
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
        self, order_ref: str, items: list[tuple[int, int]]
    ) -> pb2.ReserveStockResponse:
        """Call ``ReserveStock`` for one order.

        Raises ``grpc.aio.AioRpcError`` on transport/status failure (mapped to
        HTTP by the caller).
        """
        request = pb2.ReserveStockRequest(
            order_ref=order_ref,
            items=[pb2.ReserveItem(product_id=pid, quantity=qty) for pid, qty in items],
        )
        return await self._stub.ReserveStock(request, timeout=self._deadline)

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
