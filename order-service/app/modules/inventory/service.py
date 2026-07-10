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
import asyncio
import logging

import grpc
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.grpc_client.client import InventoryClient
from app.grpc_client.errors import grpc_to_http_status
from app.generated import order_inventory_pb2 as pb2
from app.modules.inventory.schemas import (
    ProductStockOut,
    StockUpdateOut,
    WatchCommandIn,
)

logger = logging.getLogger("order.api")

_NDJSON_MEDIA_TYPE = "application/x-ndjson"

# Protobuf StockUpdate.Kind → the friendly label sent to the WebSocket client.
_UPDATE_KIND_LABELS = {
    pb2.StockUpdate.SNAPSHOT: "snapshot",
    pb2.StockUpdate.CHANGED: "changed",
}
# WebSocketCommandIn.action → protobuf WatchCommand.Action.
_COMMAND_ACTIONS = {
    "subscribe": pb2.WatchCommand.SUBSCRIBE,
    "unsubscribe": pb2.WatchCommand.UNSUBSCRIBE,
}


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

    @staticmethod
    async def stock_watch(websocket: WebSocket, inventory: InventoryClient) -> None:
        """Bridge a WebSocket to the bidirectional ``WatchStock`` gRPC call.

        Full-duplex needs *two* concurrent pumps, mirroring the two threads on the
        gRPC server:

          * **inbound** — read JSON commands off the socket, validate them, and
            push protobuf ``WatchCommand`` onto a queue that backs the gRPC request
            stream;
          * **outbound** — iterate the gRPC response stream and forward each
            ``StockUpdate`` to the socket.

        Whichever side ends first (client closes the socket, or the gRPC call
        ends/errors) tears the other down so neither pump leaks.
        """
        await websocket.accept()
        commands: asyncio.Queue[pb2.WatchCommand | None] = asyncio.Queue()

        async def _command_stream():
            """Async iterator the gRPC client consumes; ``None`` ends the stream."""
            while True:
                command = await commands.get()
                if command is None:  # sentinel: client gone → close request stream
                    return
                yield command

        async def _pump_inbound() -> None:
            """Socket → queue: validate each command and enqueue it for gRPC.

            A malformed frame is reported back to the client and skipped, not fatal
            — only the socket closing ends the pump (which closes the request
            stream via the sentinel).
            """
            try:
                while True:
                    raw = await websocket.receive_json()
                    try:
                        command = WatchCommandIn.model_validate(raw)
                    except ValidationError as exc:
                        await websocket.send_json(
                            {"error": "invalid command", "detail": exc.errors()}
                        )
                        continue
                    logger.info("command: %s", command)
                    await commands.put(
                        pb2.WatchCommand(
                            action=_COMMAND_ACTIONS[command.action],
                            product_ids=command.product_ids,
                        )
                    )
            except WebSocketDisconnect:
                logger.info("stock-watch client disconnected")
            finally:
                await commands.put(None)  # unblock the gRPC request stream

        async def _pump_outbound() -> None:
            """gRPC updates → socket: forward each StockUpdate as JSON."""
            async for update in inventory.watch_stock(_command_stream()):
                out = StockUpdateOut(
                    product_id=update.product_id,
                    sku=update.sku,
                    name=update.name,
                    available_quantity=update.available_quantity,
                    kind=_UPDATE_KIND_LABELS[update.kind],
                )
                await websocket.send_json(out.model_dump())

        inbound = asyncio.create_task(_pump_inbound())
        outbound = asyncio.create_task(_pump_outbound())
        try:
            # Whichever pump finishes first (socket closed or gRPC stream ended)
            # ends the session; cancel the survivor so it can't leak.
            done, pending = await asyncio.wait(
                {inbound, outbound}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # asyncio.wait stores exceptions on the task rather than raising them;
            # retrieve them so a gRPC failure is logged (and never "un-retrieved").
            for task in done:
                exc = task.exception()
                if isinstance(exc, grpc.aio.AioRpcError):
                    logger.warning("WatchStock failed: %s", exc.code())
                elif exc is not None:
                    logger.warning("stock-watch pump error: %r", exc)
        finally:
            for task in (inbound, outbound):
                if not task.done():
                    task.cancel()
            await asyncio.gather(inbound, outbound, return_exceptions=True)
            try:
                await websocket.close()
            except RuntimeError:
                pass  # already closed by the client
