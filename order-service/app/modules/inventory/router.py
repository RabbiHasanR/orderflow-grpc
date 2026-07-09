"""HTTP adapter for inventory reads — thin: resolve deps, call the service."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.grpc_client.client import InventoryClient
from app.grpc_client.deps import get_inventory
from app.modules.inventory.service import InventoryService

router = APIRouter()


@router.get("/low-stock")
async def low_stock(
    inventory: Annotated[InventoryClient, Depends(get_inventory)],
    threshold: Annotated[
        int, Query(ge=0, description="Stream products with available_quantity <= this.")
    ],
    sku_prefix: Annotated[
        str, Query(description="Optional SKU prefix filter; empty means no filter.")
    ] = "",
):
    """Server-stream products at/below ``threshold`` as NDJSON, one product per line.

    Fronts the server-streaming ``WatchLowStock`` RPC. The response is
    ``application/x-ndjson`` and is produced lazily — matching products are
    written as they arrive from inventory, never buffered. An empty result is a
    valid ``200`` with an empty body.
    """
    return await InventoryService.stream_low_stock(inventory, threshold, sku_prefix)
