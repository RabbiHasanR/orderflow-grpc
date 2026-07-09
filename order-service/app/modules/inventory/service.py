"""Inventory read-side logic: front the server-streaming ``WatchLowStock`` RPC.

The gRPC stream is re-streamed to the HTTP client as **NDJSON** (one JSON object
per line) so the constant-memory property survives the REST hop — we never
collect the whole result into a list.

Error handling splits in two, which is the defining trait of a streamed
response:
  * A failure *before* the first byte still maps to an HTTP status — we probe the
    first message inside ``try/except`` before returning the ``StreamingResponse``.
  * Once bytes are flowing the status line is already on the wire, so a
    mid-stream failure can only *end* the stream; we log it and stop.
"""
import logging

import grpc
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.grpc_client.client import InventoryClient
from app.grpc_client.errors import grpc_to_http_status
from app.generated import order_inventory_pb2 as pb2
from app.modules.inventory.schemas import ProductStockOut

logger = logging.getLogger("order.api")

_NDJSON_MEDIA_TYPE = "application/x-ndjson"


def _to_line(product: pb2.ProductStock) -> bytes:
    """Serialize one protobuf ``ProductStock`` to a single NDJSON line (bytes)."""
    return ProductStockOut.model_validate(product).model_dump_json().encode() + b"\n"


class InventoryService:
    """Business logic for inventory reads; every method takes the gRPC client."""

    @staticmethod
    async def stream_low_stock(
        inventory: InventoryClient, threshold: int, sku_prefix: str = ""
    ) -> StreamingResponse:
        """Re-stream ``WatchLowStock`` to the client as NDJSON.

        Probes the first message so a pre-stream gRPC failure (e.g. inventory
        down, bad auth token) becomes an HTTP status before the body starts.
        """
        stream = inventory.watch_low_stock(threshold, sku_prefix)

        try:
            first = await anext(stream)
        except StopAsyncIteration:
            first = None  # no matching products — a valid zero-row stream
        except grpc.aio.AioRpcError as exc:
            http_status = grpc_to_http_status(exc.code())
            logger.warning("WatchLowStock failed: %s → HTTP %s", exc.code(), http_status)
            raise HTTPException(
                status_code=http_status,
                detail=f"inventory unavailable: {exc.code().name}",
            )

        async def _ndjson():
            count = 0
            try:
                if first is not None:
                    yield _to_line(first)
                    count += 1
                    async for product in stream:
                        yield _to_line(product)
                        count += 1
            except grpc.aio.AioRpcError as exc:
                # Status line already sent — can't turn this into an HTTP error.
                logger.warning(
                    "WatchLowStock interrupted mid-stream after %d rows: %s",
                    count, exc.code(),
                )
            finally:
                logger.info("WatchLowStock streamed %d products", count)

        return StreamingResponse(_ndjson(), media_type=_NDJSON_MEDIA_TYPE)
