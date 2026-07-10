"""gRPC transport adapter for InventoryService.

Translates protobuf to/from the service-layer dataclasses; all business logic
lives in ``inventory_app.services``. Business outcomes (insufficient stock,
unknown product, non-positive quantity) are returned as ``success=False`` with
per-item reasons; gRPC error statuses are reserved for malformed requests.
"""
import threading
import time
from collections.abc import Iterator

import grpc
from django.conf import settings

from generated import order_inventory_pb2 as pb2
from generated import order_inventory_pb2_grpc as pb2_grpc
from inventory_app.services import (
    ReserveItem,
    fetch_stock,
    reserve_stock,
    stream_low_stock,
)


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

    def WatchStock(
        self,
        request_iterator: Iterator[pb2.WatchCommand],
        context: grpc.ServicerContext,
    ) -> Iterator[pb2.StockUpdate]:
        """Bidirectional: subscribe/unsubscribe in, live stock updates out.

        The two streams are **independent** — this is what separates bidi from the
        other three shapes. On a *sync* server (D-021) one thread cannot both
        block on ``for cmd in request_iterator`` and poll+``yield`` responses, so
        we split the duplex across two threads:

          * a **background reader thread** drains ``request_iterator``, mutating a
            shared ``watch_set`` (subscribe adds, unsubscribe removes) under a lock
            and marking freshly-subscribed ids for an immediate snapshot;
          * **this generator** (running on the RPC's own pool thread) polls the
            current watch set every ``GRPC_WATCH_POLL_SECONDS``, emitting a
            ``SNAPSHOT`` for newly-added ids and a ``CHANGED`` for any watched
            product whose ``available_quantity`` differs from the last value sent.

        Teardown is client-driven (there is no deadline on a live watch): the loop
        exits when the request stream ends (reader sets ``stop``) or the client
        cancels (``context.is_active()`` goes False).
        """
        watch_set: set[int] = set()
        newly_added: set[int] = set()
        last_seen: dict[int, int] = {}
        lock = threading.Lock()
        stop = threading.Event()

        def _drain_commands() -> None:
            """Consume the request stream, reshaping the shared watch set."""
            try:
                for command in request_iterator:
                    ids = set(command.product_ids)
                    with lock:
                        if command.action == pb2.WatchCommand.SUBSCRIBE:
                            watch_set.update(ids)
                            newly_added.update(ids)  # snapshot on next tick
                        else:  # UNSUBSCRIBE
                            watch_set.difference_update(ids)
                            newly_added.difference_update(ids)
            finally:
                stop.set()  # request stream closed → tell the poll loop to finish

        reader = threading.Thread(target=_drain_commands, daemon=True)
        reader.start()

        poll_seconds = settings.GRPC_WATCH_POLL_SECONDS
        try:
            while context.is_active() and not stop.is_set():
                # Copy-and-clear the shared state atomically so the reader thread
                # never mutates it mid-tick.
                with lock:
                    watched = set(watch_set)
                    fresh = set(newly_added)
                    newly_added.clear()
                    # Forget quantities for ids no longer watched (a later
                    # re-subscribe then re-snapshots from scratch).
                    for pid in list(last_seen):
                        if pid not in watched:
                            del last_seen[pid]

                for row in fetch_stock(watched):
                    if row.product_id in fresh:
                        kind = pb2.StockUpdate.SNAPSHOT
                    elif last_seen.get(row.product_id) != row.available_quantity:
                        kind = pb2.StockUpdate.CHANGED
                    else:
                        continue  # unchanged and already snapshotted → stay quiet
                    last_seen[row.product_id] = row.available_quantity
                    yield pb2.StockUpdate(
                        product_id=row.product_id,
                        sku=row.sku,
                        name=row.name,
                        available_quantity=row.available_quantity,
                        kind=kind,
                    )

                stop.wait(poll_seconds)  # wake early if the request stream closes
        finally:
            stop.set()  # ensure the reader unblocks even if the client cancelled
