"""gRPC client to inventory-service: channel, stub, and a typed helper.

order-service is a **gRPC client** (project goal). It uses ``grpc.aio`` so the
RPC leg yields to the FastAPI event loop instead of blocking it (decision D-030).

Load balancing (project goal #3) is **client-side, no proxy**: the channel uses
the ``round_robin`` policy via service config. Today there is a single inventory
replica (D-026), so round-robin is a no-op with one backend — but it is already
wired, so distributing across replicas later is purely a runtime change:
``docker compose up --scale inventory-service=2`` makes the ``inventory-service``
DNS name resolve to multiple container IPs, and round_robin fans requests across
them with **no code change**. That is why the target uses the ``dns:///`` scheme:
the DNS resolver re-resolves and picks up new/updated replicas automatically.
"""
import logging

import grpc
from grpc.aio import Channel

from app.config import Settings
from app.grpc_client.interceptors import AuthClientInterceptor
from app.generated import order_inventory_pb2 as pb2
from app.generated import order_inventory_pb2_grpc as pb2_grpc

logger = logging.getLogger("order.grpc")

# Ask gRPC to load-balance across all resolved addresses (round-robin), rather
# than pinning to the first one (the default "pick_first").
_ROUND_ROBIN_SERVICE_CONFIG = '{"loadBalancingConfig":[{"round_robin":{}}]}'


def build_channel(settings: Settings) -> Channel:
    """Create the round-robin ``grpc.aio`` channel with the auth interceptor.

    Args:
        settings: Runtime settings (inventory targets + auth token + deadline).

    Returns:
        An open async channel; the caller owns its lifecycle (closed on shutdown).
    """
    # dns:/// so the resolver returns *all* replica IPs behind the name and
    # re-resolves over time; round_robin then distributes across them.
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
        """Bind the stub to a channel and remember the per-call deadline.

        Args:
            channel: The shared round-robin channel.
            deadline_seconds: Per-call timeout → ``DEADLINE_EXCEEDED`` if exceeded.
        """
        self._stub = pb2_grpc.InventoryServiceStub(channel)
        self._deadline = deadline_seconds

    async def reserve_stock(
        self, order_ref: str, items: list[tuple[int, int]]
    ) -> pb2.ReserveStockResponse:
        """Call ``ReserveStock`` for one order.

        Args:
            order_ref: The order id, stored by inventory as the cross-service ref.
            items: ``(product_id, quantity)`` pairs to reserve.

        Returns:
            The ``ReserveStockResponse`` (``success`` + per-item results).

        Raises:
            grpc.aio.AioRpcError: On any transport/status failure — mapped to an
                HTTP error by the caller (see main.py failure mapping).
        """
        request = pb2.ReserveStockRequest(
            order_ref=order_ref,
            items=[pb2.ReserveItem(product_id=pid, quantity=qty) for pid, qty in items],
        )
        return await self._stub.ReserveStock(request, timeout=self._deadline)
