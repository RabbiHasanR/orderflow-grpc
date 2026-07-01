"""FastAPI app — the public REST edge of OrderFlow.

A single ``POST /orders`` **fans out** over gRPC to inventory's ``ReserveStock``
and only persists the order if the reservation succeeds. The two services own
separate databases; there is no distributed transaction — consistency is
coordinated by the reservation *result*, not a foreign key (see workflow.md).

Failure mapping (workflow.md → HTTP):
  * bad body ............. Pydantic → 422 (before any hop)
  * success == False ..... 409 + per-item reasons (RPC still OK; a business no)
  * UNAVAILABLE .......... 503 (backend unreachable)
  * DEADLINE_EXCEEDED .... 504 (RPC deadline)
  * UNAUTHENTICATED ...... 502 (our token is wrong — a server-config problem)

Known v1 gap (spec 002): the reservation commits in inventory *before* the order
is persisted here, so a failure after commit orphans a reservation. The future
fix is a saga/outbox using inventory's ``StockReservation.RELEASED`` status.
"""
import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

import grpc
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import engine, get_session, init_models
from app.grpc_client.client import InventoryClient, build_channel
from app.models import Order, OrderItem
from app.schemas import ItemResultOut, OrderCreate, OrderOut

logger = logging.getLogger("order.api")

# gRPC status → HTTP status. Anything unlisted falls back to 502 (a broken hop is
# an upstream/config fault, not the client's).
_GRPC_TO_HTTP = {
    grpc.StatusCode.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED: status.HTTP_504_GATEWAY_TIMEOUT,
    grpc.StatusCode.UNAUTHENTICATED: status.HTTP_502_BAD_GATEWAY,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables and open the shared gRPC channel; tear both down on exit."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()

    await init_models()  # D-033: create_all at startup (no Alembic for v1)

    channel = build_channel(settings)
    app.state.inventory = InventoryClient(channel, settings.grpc_deadline_seconds)
    logger.info("order-service ready")
    try:
        yield
    finally:
        await channel.close()
        await engine.dispose()


app = FastAPI(title="OrderFlow — order-service", lifespan=lifespan)


def get_inventory() -> InventoryClient:
    """FastAPI dependency returning the process-wide inventory gRPC client."""
    return app.state.inventory


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe for compose ``depends_on`` / the container healthcheck."""
    return {"status": "ok"}


@app.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    inventory: Annotated[InventoryClient, Depends(get_inventory)],
) -> Order:
    """Reserve stock over gRPC, then persist the order only if it succeeded.

    Args:
        payload: Validated order body (non-empty items, positive fields).
        session: Async DB session for order-db.
        inventory: gRPC client to inventory-service.

    Returns:
        The persisted :class:`Order` (serialized as :class:`OrderOut`).

    Raises:
        HTTPException: ``409`` if any item cannot be reserved; ``503/504/502`` on
            a gRPC transport/status failure.
    """
    # Generate the id up front: it is the order_ref inventory stores per
    # reservation, and it must exist *before* the RPC (workflow.md step 3). We set
    # it explicitly rather than relying on the model's column default, which only
    # fires at INSERT-flush time — after the RPC.
    order_ref = str(uuid4())
    order = Order(
        id=order_ref,
        items=[OrderItem(product_id=i.product_id, quantity=i.quantity) for i in payload.items],
    )
    line_items = [(i.product_id, i.quantity) for i in payload.items]

    try:
        response = await inventory.reserve_stock(order_ref=order_ref, items=line_items)
    except grpc.aio.AioRpcError as exc:
        http_status = _GRPC_TO_HTTP.get(exc.code(), status.HTTP_502_BAD_GATEWAY)
        logger.warning("ReserveStock failed: %s → HTTP %s", exc.code(), http_status)
        raise HTTPException(status_code=http_status, detail=f"inventory unavailable: {exc.code().name}")

    if not response.success:
        # Business rejection (insufficient stock / unknown product / bad qty). The
        # RPC itself was OK; nothing was persisted on either side.
        reasons = [
            ItemResultOut(product_id=r.product_id, reserved=r.reserved, reason=r.reason).model_dump()
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


@app.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Order:
    """Read back a persisted order (makes the service demonstrable without psql).

    Args:
        order_id: The order id (UUID string) returned by ``POST /orders``.
        session: Async DB session for order-db.

    Returns:
        The matching :class:`Order`.

    Raises:
        HTTPException: ``404`` if no order has that id.
    """
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    return order
