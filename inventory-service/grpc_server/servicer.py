"""gRPC transport adapter for InventoryService.

Translates protobuf to/from the service-layer dataclasses; all business logic
lives in ``inventory_app.services``. Business outcomes (insufficient stock,
unknown product, non-positive quantity) are returned as ``success=False`` with
per-item reasons; gRPC error statuses are reserved for malformed requests.
"""
from collections.abc import Iterator

import grpc

from generated import order_inventory_pb2 as pb2
from generated import order_inventory_pb2_grpc as pb2_grpc
from inventory_app.services import ReserveItem, reserve_stock, stream_low_stock


class InventoryServicer(pb2_grpc.InventoryServiceServicer):
    """Implements the InventoryService RPCs defined in order_inventory.proto."""

    def ReserveStock(
        self,
        request: pb2.ReserveStockRequest,
        context: grpc.ServicerContext,
    ) -> pb2.ReserveStockResponse:
        """Adapt the request, run the reservation, and adapt the result back."""
        if not request.order_ref:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "order_ref is required")
        if not request.items:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "items must not be empty")

        items = [
            ReserveItem(product_id=item.product_id, quantity=item.quantity)
            for item in request.items
        ]
        outcome = reserve_stock(order_ref=request.order_ref, items=items)

        return pb2.ReserveStockResponse(
            success=outcome.success,
            results=[
                pb2.ItemOutcome(
                    product_id=result.product_id,
                    reserved=result.reserved,
                    reason=result.reason,
                )
                for result in outcome.results
            ],
        )

    def ReserveStockBulk(
        self,
        request_iterator: Iterator[pb2.BulkReserveItem],
        context: grpc.ServicerContext,
    ) -> pb2.BulkReserveSummary:
        """Read a stream of reservation lines, reserve each best-effort, reply once.

        Client-streaming: consume ``request_iterator`` to exhaustion, then return
        a single ``BulkReserveSummary``. Each line is reserved on its own by
        delegating to the same ``reserve_stock`` the unary path uses, with a
        one-item list — so every line gets its own ``transaction.atomic()`` and
        one failing line never rolls back the others (best-effort per item).

        A malformed line (empty ``order_ref``) is recorded as a failed outcome
        rather than aborting the whole stream, keeping the best-effort contract.
        """
        results: list[pb2.BulkItemOutcome] = []
        reserved_count = 0

        for item in request_iterator:
            if not item.order_ref:
                results.append(
                    pb2.BulkItemOutcome(
                        order_ref=item.order_ref,
                        product_id=item.product_id,
                        reserved=False,
                        reason="order_ref is required",
                    )
                )
                continue

            outcome = reserve_stock(
                order_ref=item.order_ref,
                items=[ReserveItem(product_id=item.product_id, quantity=item.quantity)],
            )
            result = outcome.results[0]
            if result.reserved:
                reserved_count += 1
            results.append(
                pb2.BulkItemOutcome(
                    order_ref=item.order_ref,
                    product_id=result.product_id,
                    reserved=result.reserved,
                    reason=result.reason,
                )
            )

        return pb2.BulkReserveSummary(
            total=len(results),
            reserved_count=reserved_count,
            failed_count=len(results) - reserved_count,
            results=results,
        )

    def WatchLowStock(
        self,
        request: pb2.LowStockQuery,
        context: grpc.ServicerContext,
    ) -> Iterator[pb2.ProductStock]:
        """Stream every product at/below ``threshold``, one message at a time.

        Server-streaming: instead of returning a single message we ``yield`` one
        ``ProductStock`` per matching row, pulled lazily from the service layer's
        cursor-backed generator. An empty result is a valid zero-message stream,
        not an error — gRPC error statuses stay reserved for malformed requests.

        We check ``context.is_active()`` before each yield so a client that
        cancels or hits its deadline stops the DB iteration promptly instead of
        walking the whole catalog for nobody.
        """
        if request.threshold < 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "threshold must be >= 0")

        for row in stream_low_stock(request.threshold, request.sku_prefix):
            if not context.is_active():  # client cancelled or deadline exceeded
                return
            yield pb2.ProductStock(
                product_id=row.product_id,
                sku=row.sku,
                name=row.name,
                available_quantity=row.available_quantity,
            )
