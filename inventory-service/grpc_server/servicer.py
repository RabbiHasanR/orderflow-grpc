"""gRPC transport adapter for InventoryService.

Translates protobuf to/from the service-layer dataclasses; all business logic
lives in ``inventory_app.services``. Business outcomes (insufficient stock,
unknown product, non-positive quantity) are returned as ``success=False`` with
per-item reasons; gRPC error statuses are reserved for malformed requests.
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
