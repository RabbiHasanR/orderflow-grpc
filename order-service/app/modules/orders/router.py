"""HTTP adapter for orders — thin: resolve deps, call the service, return a model."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.grpc_client.client import InventoryClient
from app.modules.orders.models import Order
from app.modules.orders.schemas import (
    BulkOrderCreate,
    BulkOrderOut,
    OrderCreate,
    OrderOut,
)
from app.modules.orders.service import OrderService

router = APIRouter()


def get_inventory(request: Request) -> InventoryClient:
    """Return the process-wide inventory gRPC client opened in the lifespan."""
    return request.app.state.inventory


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    inventory: Annotated[InventoryClient, Depends(get_inventory)],
) -> Order:
    """Reserve stock over gRPC, then persist the order only if it succeeded."""
    return await OrderService.create_order(session, inventory, payload)


@router.post("/bulk", response_model=BulkOrderOut, status_code=status.HTTP_200_OK)
async def create_orders_bulk(
    payload: BulkOrderCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    inventory: Annotated[InventoryClient, Depends(get_inventory)],
) -> BulkOrderOut:
    """Stream many orders to the client-streaming ``ReserveStockBulk`` RPC.

    Returns ``200`` with a per-order summary (best-effort): each order is
    ``persisted``, ``partial``, or ``failed``. Unlike ``POST /orders``, a mixed
    batch is not an error — the outcome lives in the body.
    """
    return await OrderService.create_orders_bulk(session, inventory, payload)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Order:
    """Read back a persisted order by id; ``404`` if not found."""
    return await OrderService.get_order(session, order_id)
