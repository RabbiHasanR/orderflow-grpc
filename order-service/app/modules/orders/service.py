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
from app.grpc_client.errors import grpc_to_http_status
from app.modules.orders.models import Order, OrderItem
from app.modules.orders.schemas import (
    BulkOrderCreate,
    BulkOrderOut,
    BulkOrderResult,
    ItemResultOut,
    OrderCreate,
)

logger = logging.getLogger("order.api")


class OrderService:
    """Business logic for orders; every method takes an injected session."""

    @staticmethod
    async def create_order(
        session: AsyncSession,
        inventory: InventoryClient,
        payload: OrderCreate,
        idempotency_key: str | None = None,
    ) -> Order:
        """Reserve stock over gRPC, then persist the order only if it succeeded.

        Idempotent when the caller supplies an ``idempotency_key`` (the
        ``Idempotency-Key`` header): the key doubles as the order id, so a retry
        returns the already-persisted order instead of reserving again, and the
        same key is forwarded to inventory as a second line of defence. If the
        order fails to persist *after* a successful reserve, we compensate with
        ``ReleaseStock`` so held stock is not stranded (the reserve-then-persist
        saga's rollback).
        """
        # The key doubles as the order id / order_ref (workflow.md step 3). Without
        # one, fall back to a fresh uuid4 — a single call is still safe, but retries
        # can't be deduped (that needs a client-supplied key).
        order_ref = idempotency_key or str(uuid4())

        # Idempotent short-circuit: this key already produced a persisted order.
        existing = await session.get(Order, order_ref)
        if existing is not None:
            logger.info("order %s already exists — idempotent replay", order_ref)
            return existing

        order = Order(
            id=order_ref,
            items=[
                OrderItem(product_id=i.product_id, quantity=i.quantity)
                for i in payload.items
            ],
        )
        line_items = [(i.product_id, i.quantity) for i in payload.items]

        try:
            response = await inventory.reserve_stock(
                order_ref=order_ref, items=line_items, idempotency_key=order_ref
            )
        except grpc.aio.AioRpcError as exc:
            http_status = grpc_to_http_status(exc.code())
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

        try:
            session.add(order)
            await session.commit()
            await session.refresh(order)
        except Exception:
            # Reserve succeeded but we couldn't persist the order — compensate so
            # the reserved stock is returned instead of stranded (D-036 fix).
            await session.rollback()
            await OrderService._release_quietly(inventory, order_ref)
            logger.exception("persist failed after reserve; released %s", order_ref)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="failed to persist order",
            )

        logger.info("order %s created (%d items)", order.id, len(line_items))
        return order

    @staticmethod
    async def _release_quietly(inventory: InventoryClient, order_ref: str) -> None:
        """Best-effort ``ReleaseStock`` compensation; never raise over a failure.

        If the release itself fails (inventory down), we log and move on rather
        than masking the original error — the reservation is left for a future
        reconciliation sweep (a documented follow-on, Phase 6).
        """
        try:
            await inventory.release_stock(order_ref)
        except grpc.aio.AioRpcError as exc:
            logger.error("compensation ReleaseStock(%s) failed: %s", order_ref, exc.code())

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
            http_status = grpc_to_http_status(exc.code())
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
        stranded_refs: list[str] = []
        for order in orders:
            items = outcomes_by_ref[order.id]
            if items and all(i.reserved for i in items):
                session.add(order)
                persisted_count += 1
                order_status = "persisted"
            elif any(i.reserved for i in items):
                # Partial: some lines reserved but the order isn't saved, so those
                # reserved lines would be stranded on inventory (D-036). Release them.
                order_status = "partial"
                stranded_refs.append(order.id)
            else:
                order_status = "failed"  # nothing reserved → nothing to release
            results.append(
                BulkOrderResult(order_ref=order.id, status=order_status, items=items)
            )

        await session.commit()

        # Compensate partial orders: return their stranded reserved stock. Done
        # after the commit so a release failure can't roll back persisted orders.
        for ref in stranded_refs:
            await OrderService._release_quietly(inventory, ref)
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
