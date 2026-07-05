"""gRPC client to inventory-service: channel, stub, and a typed helper.

Uses ``grpc.aio`` (D-030) with client-side ``round_robin`` load balancing over a
``dns:///`` target, so scaling inventory replicas needs no code change.
"""
import logging

import grpc
from grpc.aio import Channel

from app.config import Settings
from app.grpc_client.interceptors import AuthClientInterceptor
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
        interceptors=[AuthClientInterceptor(settings.grpc_auth_token)],
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
