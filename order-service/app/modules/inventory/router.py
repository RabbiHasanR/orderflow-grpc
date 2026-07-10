"""HTTP adapter for inventory reads — thin: resolve deps, call the service."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket
from fastapi.responses import HTMLResponse

from app.grpc_client.client import InventoryClient
from app.grpc_client.deps import get_inventory
from app.modules.inventory.demo import STOCK_WATCH_DEMO_HTML
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


@router.websocket("/stock-watch")
async def stock_watch(
    websocket: WebSocket,
    inventory: Annotated[InventoryClient, Depends(get_inventory)],
) -> None:
    """Full-duplex live stock watch, fronting the bidirectional ``WatchStock`` RPC.

    Send ``{"action": "subscribe"|"unsubscribe", "product_ids": [..]}`` frames;
    receive ``{"product_id", "sku", "name", "available_quantity", "kind"}`` frames
    (``kind`` is ``snapshot`` on subscribe, ``changed`` on a later stock change).
    Both directions flow independently over one connection.
    """
    await InventoryService.stock_watch(websocket, inventory)


@router.get("/stock-watch/demo", response_class=HTMLResponse)
async def stock_watch_demo() -> str:
    """Tiny self-contained page to try the ``/stock-watch`` WebSocket in a browser."""
    return STOCK_WATCH_DEMO_HTML
