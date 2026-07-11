"""gRPC transport adapter for InventoryService.

Translates protobuf to/from the service-layer dataclasses; all business logic
lives in ``inventory_app.services``. Business outcomes (insufficient stock,
unknown product, non-positive quantity) are returned as ``success=False`` with
per-item reasons; gRPC error statuses are reserved for malformed requests.
"""
import logging
import queue
import threading
from collections.abc import Iterator

import grpc
from django.conf import settings

from generated import order_inventory_pb2 as pb2
from generated import order_inventory_pb2_grpc as pb2_grpc
from grpc_server.notifications import listen_stock_changes
from inventory_app.services import (
    ReserveItem,
    fetch_stock,
    release_stock,
    reserve_stock,
    stream_low_stock,
)

# Transport/stream-lifecycle logger (spec 013), shared with the interceptors.
# Domain outcomes are logged one layer down on ``inventory.service``.
logger = logging.getLogger("inventory.grpc")


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
        outcome = reserve_stock(
            order_ref=request.order_ref,
            items=items,
            idempotency_key=request.idempotency_key,
        )

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

    def ReleaseStock(
        self,
        request: pb2.ReleaseStockRequest,
        context: grpc.ServicerContext,
    ) -> pb2.ReleaseStockResponse:
        """Release a previously reserved order, returning its stock (compensation)."""
        if not request.order_ref:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "order_ref is required")

        outcome = release_stock(order_ref=request.order_ref)
        return pb2.ReleaseStockResponse(
            released=outcome.released,
            released_count=outcome.released_count,
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
                logger.warning(
                    "ReserveStockBulk: dropping malformed line (product_id=%s, "
                    "missing order_ref)", item.product_id,
                )
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

        logger.info(
            "ReserveStockBulk summary: %d line(s), %d reserved, %d failed",
            len(results), reserved_count, len(results) - reserved_count,
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
        other three shapes. Updates are **push-based** (spec 008, D-039): rather
        than polling the DB on a timer, we react to Postgres ``NOTIFY`` events
        fired by the ``stock_changed`` trigger. On a *sync* server (D-021) one
        thread cannot block on several things at once, so the work is split across
        three, all feeding a single ``events`` queue that this generator drains:

          * a **reader thread** drains ``request_iterator``, mutating a shared
            ``watch_set`` (subscribe adds, unsubscribe removes) under a lock and
            queuing a ``snapshot`` marker so newly-watched ids get an initial value;
          * a **listener thread** LISTENs on ``stock_changed`` and, for a
            notification about a *watched* product, queues a ``changed`` marker
            (plus a ``resync`` after any reconnect, to recover missed notifies);
          * **this generator** blocks on the queue and, per marker, reads just the
            affected product(s) via ``fetch_stock`` and yields ``SNAPSHOT`` /
            ``CHANGED`` — no work at all while nothing changes.

        Teardown is client-driven (there is no deadline on a live watch): ``stop``
        is set when the request stream ends or the client cancels
        (``context.is_active()`` goes False), unblocking both helper threads.
        """
        peer = context.peer()
        logger.info("WatchStock stream opened peer=%s", peer)

        watch_set: set[int] = set()
        newly_added: set[int] = set()
        last_seen: dict[int, int] = {}
        lock = threading.Lock()
        stop = threading.Event()
        events: queue.Queue[tuple[str, int | None]] = queue.Queue()
        tick = settings.GRPC_WATCH_TICK_SECONDS

        def _drain_commands() -> None:
            """Consume the request stream, reshaping the shared watch set."""
            try:
                for command in request_iterator:
                    ids = set(command.product_ids)
                    with lock:
                        if command.action == pb2.WatchCommand.SUBSCRIBE:
                            watch_set.update(ids)
                            newly_added.update(ids)
                            events.put(("snapshot", None))  # emit initial values
                            logger.info(
                                "WatchStock subscribe peer=%s ids=%s (watching %d)",
                                peer, sorted(ids), len(watch_set),
                            )
                        else:  # UNSUBSCRIBE
                            watch_set.difference_update(ids)
                            newly_added.difference_update(ids)
                            logger.info(
                                "WatchStock unsubscribe peer=%s ids=%s (watching %d)",
                                peer, sorted(ids), len(watch_set),
                            )
            except Exception:
                # Don't die silently — a crashed reader would freeze the watch set.
                logger.exception("WatchStock reader thread failed peer=%s", peer)
            finally:
                stop.set()  # request stream closed → end the watch

        def _drain_notifications() -> None:
            """Turn Postgres NOTIFYs into queue markers for watched products."""
            try:
                for event in listen_stock_changes(stop, tick):
                    if event.kind == "resync":
                        events.put(("resync", None))
                        continue
                    with lock:
                        watched = event.product_id in watch_set
                    if watched:  # ignore changes to products nobody here is watching
                        events.put(("changed", event.product_id))
            except Exception:
                logger.exception("WatchStock listener thread failed peer=%s", peer)
                stop.set()  # can't receive updates anymore → end the watch

        reader = threading.Thread(target=_drain_commands, daemon=True)
        listener = threading.Thread(target=_drain_notifications, daemon=True)
        reader.start()
        listener.start()

        def _rows(ids: set[int]) -> list:
            """Read the still-watched subset of ``ids`` from the DB."""
            with lock:
                watched = watch_set & ids
            return fetch_stock(watched)

        def _updates(ids: set[int], *, snapshot: bool) -> Iterator[pb2.StockUpdate]:
            """Yield an update per product, remembering the value we sent.

            ``snapshot=True`` always emits (a fresh subscribe wants the current
            value); otherwise we emit only when the quantity actually differs from
            what we last sent, so a duplicate NOTIFY produces nothing.
            """
            for row in _rows(ids):
                if not snapshot and last_seen.get(row.product_id) == row.available_quantity:
                    continue
                last_seen[row.product_id] = row.available_quantity
                yield pb2.StockUpdate(
                    product_id=row.product_id,
                    sku=row.sku,
                    name=row.name,
                    available_quantity=row.available_quantity,
                    kind=pb2.StockUpdate.SNAPSHOT if snapshot else pb2.StockUpdate.CHANGED,
                )

        try:
            while context.is_active() and not stop.is_set():
                try:
                    kind, product_id = events.get(timeout=tick)
                except queue.Empty:
                    continue  # timed out only to re-check stop / is_active
                if kind == "snapshot":
                    with lock:
                        fresh = set(newly_added)
                        newly_added.clear()
                    yield from _updates(fresh, snapshot=True)
                elif kind == "changed":
                    yield from _updates({product_id}, snapshot=False)
                elif kind == "resync":
                    with lock:
                        watched = set(watch_set)
                    yield from _updates(watched, snapshot=False)
        finally:
            stop.set()  # unblock the reader + listener threads
            listener.join(timeout=tick * 2)  # let its DB connection close promptly
            logger.info("WatchStock stream closed peer=%s", peer)
