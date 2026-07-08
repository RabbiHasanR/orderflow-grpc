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
from app.modules.orders.schemas import (
    BulkOrderCreate,
    BulkOrderOut,
    BulkOrderResult,
    ItemResultOut,
    OrderCreate,
)

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
    async def create_orders_bulk(
        session: AsyncSession,
        inventory: InventoryClient,
        payload: BulkOrderCreate,
    ) -> BulkOrderOut:
        """Stream many orders to inventory's client-streaming RPC, then persist.

        Each order gets an ``order_ref`` up front; all lines are flattened into a
        single stream sent to ``ReserveStockBulk``. The server reserves each line
        best-effort, so an order can come back fully, partially, or not reserved.
        We persist only orders whose every line reserved (``persisted``); the rest
        are reported ``partial``/``failed`` and left unsaved — see D-036 for the
        orphaned-reservation caveat that ``partial`` implies.
        """
        # Build orders + their ref up front (the ref is what inventory stores).
        orders = [
            Order(
                id=str(uuid4()),
                items=[
                    OrderItem(product_id=i.product_id, quantity=i.quantity)
                    for i in order.items
                ],
            )
            for order in payload.orders
        ]
        lines = [
            (order.id, item.product_id, item.quantity)
            for order in orders
            for item in order.items
        ]

        try:
            summary = await inventory.reserve_stock_bulk(lines)
        except grpc.aio.AioRpcError as exc:
            http_status = _GRPC_TO_HTTP.get(exc.code(), status.HTTP_502_BAD_GATEWAY)
            logger.warning("ReserveStockBulk failed: %s → HTTP %s", exc.code(), http_status)
            raise HTTPException(
                status_code=http_status,
                detail=f"inventory unavailable: {exc.code().name}",
            )

        # Regroup the flat per-line outcomes back under their order_ref.
        outcomes_by_ref: dict[str, list[ItemResultOut]] = {order.id: [] for order in orders}
        for r in summary.results:
            outcomes_by_ref[r.order_ref].append(
                ItemResultOut(product_id=r.product_id, reserved=r.reserved, reason=r.reason)
            )

        results: list[BulkOrderResult] = []
        persisted_count = 0
        for order in orders:
            items = outcomes_by_ref[order.id]
            if items and all(i.reserved for i in items):
                session.add(order)
                persisted_count += 1
                order_status = "persisted"
            else:
                # Partial or full failure: nothing saved on our side. A partial
                # order leaves reserved stock stranded on inventory (D-036).
                order_status = "failed" if not any(i.reserved for i in items) else "partial"
            results.append(
                BulkOrderResult(order_ref=order.id, status=order_status, items=items)
            )

        await session.commit()
        logger.info(
            "bulk orders: %d/%d persisted (%d lines)",
            persisted_count,
            len(orders),
            len(lines),
        )
        return BulkOrderOut(
            total_orders=len(orders),
            persisted_count=persisted_count,
            results=results,
        )

    @staticmethod
    async def get_order(session: AsyncSession, order_id: str) -> Order:
        """Read back a persisted order by id; ``404`` if not found."""
        order = await session.get(Order, order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="order not found"
            )
        return order
