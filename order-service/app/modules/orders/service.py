"""Order domain logic: the reserve-then-persist flow, isolated from FastAPI.

``POST /orders`` fans out over gRPC to inventory's ``ReserveStock`` and persists
the order only if the reservation succeeds. Separate DBs, no distributed
transaction — consistency is coordinated by the reservation result (workflow.md).

Keeping this in a service (not the router) makes the orchestration testable
without spinning up FastAPI, and keeps the router a thin HTTP adapter.
"""
import logging
from uuid import uuid4

import grpc
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.grpc_client.client import InventoryClient
from app.modules.orders.models import Order, OrderItem
from app.modules.orders.schemas import ItemResultOut, OrderCreate

logger = logging.getLogger("order.api")

# gRPC status → HTTP status; anything unlisted falls back to 502.
_GRPC_TO_HTTP = {
    grpc.StatusCode.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED: status.HTTP_504_GATEWAY_TIMEOUT,
    grpc.StatusCode.UNAUTHENTICATED: status.HTTP_502_BAD_GATEWAY,
}


class OrderService:
    """Business logic for orders; every method takes an injected session."""

    @staticmethod
    async def create_order(
        session: AsyncSession,
        inventory: InventoryClient,
        payload: OrderCreate,
    ) -> Order:
        """Reserve stock over gRPC, then persist the order only if it succeeded."""
        # Generate the id up front: it is the order_ref inventory stores, and must
        # exist before the RPC (workflow.md step 3).
        order_ref = str(uuid4())
        order = Order(
            id=order_ref,
            items=[
                OrderItem(product_id=i.product_id, quantity=i.quantity)
                for i in payload.items
            ],
        )
        line_items = [(i.product_id, i.quantity) for i in payload.items]

        try:
            response = await inventory.reserve_stock(order_ref=order_ref, items=line_items)
        except grpc.aio.AioRpcError as exc:
            http_status = _GRPC_TO_HTTP.get(exc.code(), status.HTTP_502_BAD_GATEWAY)
            logger.warning("ReserveStock failed: %s → HTTP %s", exc.code(), http_status)
            raise HTTPException(
                status_code=http_status,
                detail=f"inventory unavailable: {exc.code().name}",
            )

        if not response.success:
            # Business rejection: RPC was OK, nothing persisted on either side.
            reasons = [
                ItemResultOut(
                    product_id=r.product_id, reserved=r.reserved, reason=r.reason
                ).model_dump()
                for r in response.results
            ]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "stock reservation failed", "results": reasons},
            )

        session.add(order)
        await session.commit()
        await session.refresh(order)
        logger.info("order %s created (%d items)", order.id, len(line_items))
        return order

    @staticmethod
    async def get_order(session: AsyncSession, order_id: str) -> Order:
        """Read back a persisted order by id; ``404`` if not found."""
        order = await session.get(Order, order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            )
        return order
