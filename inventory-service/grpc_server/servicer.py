"""gRPC servicer — the transport adapter for InventoryService.

This is intentionally thin. It translates protobuf messages to/from the plain
dataclasses understood by the service layer and routes the call into
``inventory_app.services.reserve_stock``. All business logic — locking,
transactions, all-or-nothing semantics — lives in the service layer, not here.

Error model: expected *business* outcomes (insufficient stock, unknown product,
non-positive quantity) are returned in the response body as ``success=False``
with per-item reasons, because a batch may fail for several distinct reasons that
a single gRPC status code could not express. gRPC error statuses are reserved for
*malformed* requests (e.g. missing order_ref or no items → INVALID_ARGUMENT).
"""
import grpc

from generated import order_inventory_pb2 as pb2
from generated import order_inventory_pb2_grpc as pb2_grpc
from inventory_app.services import ReserveItem, reserve_stock


class InventoryServicer(pb2_grpc.InventoryServiceServicer):
    """Implements the InventoryService RPCs defined in order_inventory.proto."""

    def ReserveStock(
        self,
        request: pb2.ReserveStockRequest,
        context: grpc.ServicerContext,
    ) -> pb2.ReserveStockResponse:
        """Adapt the request, run the reservation, and adapt the result back.

        Args:
            request: The incoming reservation request (order_ref + items).
            context: The gRPC call context, used to abort on malformed input.

        Returns:
            A ``ReserveStockResponse`` whose ``success`` is True only if every
            item was reserved.
        """
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
