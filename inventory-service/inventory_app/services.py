"""Reservation business logic (the service layer).

Framework-agnostic: no gRPC/protobuf here. Concurrency safety comes from
``select_for_update()`` (row-level lock) inside ``transaction.atomic()``, so
concurrent orders cannot both read the same quantity and oversell. Reservation
is all-or-nothing: any unsatisfiable line rolls back the whole batch, but a
per-item breakdown is still returned.
"""
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import F

from .models import Product, StockReservation

# Domain-event logger (spec 013). Distinct from ``inventory.grpc`` (transport):
# this narrates *what the business did*, mirroring order-service's log style.
logger = logging.getLogger("inventory.service")


@dataclass(frozen=True)
class ReserveItem:
    """A single requested line: reserve ``quantity`` of product ``product_id``."""

    product_id: int
    quantity: int


@dataclass(frozen=True)
class ItemOutcome:
    """Per-item result. ``reason`` is empty when ``reserved`` is True."""

    product_id: int
    reserved: bool
    reason: str = ""


@dataclass(frozen=True)
class ReservationOutcome:
    """Overall result: ``success`` is True only if every item was reserved."""

    success: bool
    results: list[ItemOutcome] = field(default_factory=list)


@dataclass(frozen=True)
class ReleaseOutcome:
    """Result of releasing a reservation. ``released`` is False when nothing was
    still ``RESERVED`` for the order (already released, or never reserved)."""

    released: bool
    released_count: int = 0


@dataclass(frozen=True)
class ProductStockRow:
    """A single product's current stock, streamed by :func:`stream_low_stock`."""

    product_id: int
    sku: str
    name: str
    available_quantity: int


def reserve_stock(
    order_ref: str, items: Sequence[ReserveItem], idempotency_key: str = ""
) -> ReservationOutcome:
    """Atomically reserve stock for an order, all-or-nothing and idempotent.

    Each product row is locked, checked, and (if sufficient) decremented with a
    matching ``StockReservation``. If any item fails — unknown product,
    non-positive quantity, or insufficient stock — the transaction rolls back and
    ``success`` is False, while ``results`` still reflects the per-item evaluation.

    Idempotency: when ``idempotency_key`` is set and it already has active
    (``RESERVED``) reservations, the original reserve succeeded (this path is
    all-or-nothing), so we replay that outcome without touching stock — a client
    retry after a timeout can no longer double-reserve. A rare *concurrent*
    duplicate that slips past this replay check is caught by the partial unique
    index (``uniq_active_reservation_per_key_product``): the losing transaction
    rolls back with :class:`IntegrityError` and is reported as a failure (its
    caller then retries onto the replay path), so stock is never double-decremented.

    Args:
        order_ref: The order-service order id (cross-service reference, no FK).
        items: Line items to reserve. Duplicate ``product_id`` values work because
            stock is decremented as we go.
        idempotency_key: Stable per-attempt key. Empty disables dedup.

    Returns:
        A :class:`ReservationOutcome`; when ``success`` is False no rows persist.
    """
    if not items:
        return ReservationOutcome(success=False, results=[])

    # Lock rows in a deterministic (product_id) order so two orders touching the
    # same products in opposite order can't deadlock in Postgres.
    items = sorted(items, key=lambda i: i.product_id)

    # Idempotent replay: an existing active reservation for this key means the
    # original all-or-nothing reserve committed — return that, untouched.
    if idempotency_key:
        prior = list(
            StockReservation.objects.filter(
                idempotency_key=idempotency_key,
                status=StockReservation.Status.RESERVED,
            )
        )
        if prior:
            logger.info(
                "reserve %s: idempotent replay (key=%s, %d lines)",
                order_ref, idempotency_key, len(prior),
            )
            return ReservationOutcome(
                success=True,
                results=[ItemOutcome(r.product_id, True) for r in prior],
            )

    outcomes: list[ItemOutcome] = []
    all_ok = True

    try:
        with transaction.atomic():
            for item in items:
                if item.quantity <= 0:
                    outcomes.append(
                        ItemOutcome(item.product_id, False, "quantity must be positive")
                    )
                    all_ok = False
                    continue

                try:
                    # Row-level lock until this transaction completes.
                    product = Product.objects.select_for_update().get(pk=item.product_id)
                except Product.DoesNotExist:
                    outcomes.append(
                        ItemOutcome(item.product_id, False, "product not found")
                    )
                    all_ok = False
                    continue

                if product.available_quantity < item.quantity:
                    outcomes.append(
                        ItemOutcome(
                            item.product_id,
                            False,
                            f"insufficient stock: requested {item.quantity}, "
                            f"available {product.available_quantity}",
                        )
                    )
                    all_ok = False
                    continue

                product.available_quantity -= item.quantity
                product.save(update_fields=["available_quantity"])
                StockReservation.objects.create(
                    product=product,
                    order_ref=order_ref,
                    idempotency_key=idempotency_key,
                    quantity=item.quantity,
                    status=StockReservation.Status.RESERVED,
                )
                outcomes.append(ItemOutcome(item.product_id, True))

            if not all_ok:
                # Roll back the decrements/reservations but keep the per-item outcomes.
                transaction.set_rollback(True)
    except IntegrityError:
        # A concurrent duplicate (same key + product) won the unique index race;
        # this transaction rolled back so no stock moved. Report failure — the
        # caller's retry replays the winner's committed outcome above.
        logger.warning(
            "reserve %s: concurrent duplicate lost the unique-index race (key=%s)",
            order_ref, idempotency_key,
        )
        return ReservationOutcome(
            success=False,
            results=[
                ItemOutcome(item.product_id, False, "duplicate reservation in progress")
                for item in items
            ],
        )

    if all_ok:
        logger.info("reserved order %s: %d line(s)", order_ref, len(outcomes))
    else:
        reasons = "; ".join(
            f"{o.product_id}:{o.reason}" for o in outcomes if not o.reserved
        )
        logger.warning("reserve %s rejected: %s", order_ref, reasons)
    return ReservationOutcome(success=all_ok, results=outcomes)


def release_stock(order_ref: str) -> ReleaseOutcome:
    """Release every still-``RESERVED`` line for ``order_ref``, returning stock.

    The compensating action for the reserve-then-persist saga: order-service
    calls this when it fails to persist an order after a successful reserve, so
    the held quantity is not stranded. Idempotent — a second call (or one for an
    order that never reserved) finds nothing ``RESERVED`` and is a no-op success.

    Each line's quantity is added back with an atomic ``F()`` update (computed in
    the DB, so it can't lose a concurrent write) and its row flipped to
    ``RELEASED`` for an audit trail.

    Args:
        order_ref: The order-service order id whose reservations to release.

    Returns:
        A :class:`ReleaseOutcome` with how many lines were released.
    """
    with transaction.atomic():
        reservations = list(
            StockReservation.objects.select_for_update()
            .filter(order_ref=order_ref, status=StockReservation.Status.RESERVED)
            .order_by("product_id")
        )
        for reservation in reservations:
            Product.objects.filter(pk=reservation.product_id).update(
                available_quantity=F("available_quantity") + reservation.quantity
            )
            reservation.status = StockReservation.Status.RELEASED
            reservation.save(update_fields=["status"])

    if reservations:
        logger.info("released order %s: %d line(s)", order_ref, len(reservations))
    else:
        logger.info("release %s: nothing reserved — no-op", order_ref)
    return ReleaseOutcome(released=bool(reservations), released_count=len(reservations))


def _to_row(product: Product) -> ProductStockRow:
    """Map a ``Product`` model to the framework-agnostic :class:`ProductStockRow`."""
    return ProductStockRow(
        product_id=product.pk,
        sku=product.sku,
        name=product.name,
        available_quantity=product.available_quantity,
    )


def stream_low_stock(
    threshold: int, sku_prefix: str = ""
) -> Iterator[ProductStockRow]:
    """Yield every product with ``available_quantity <= threshold``, lazily.

    Read-only (no transaction). ``.iterator()`` streams rows straight from the DB
    cursor instead of materialising the whole queryset, so memory stays flat no
    matter how large the catalog is — this is what lets the gRPC layer forward
    the rows as a server-stream without buffering.

    Args:
        threshold: Inclusive upper bound on ``available_quantity``.
        sku_prefix: Optional SKU prefix filter; empty string means no filter.

    Yields:
        One :class:`ProductStockRow` per matching product, ordered by SKU.
    """
    logger.info(
        "low-stock scan: threshold=%d prefix=%r", threshold, sku_prefix or "",
    )
    products = Product.objects.filter(available_quantity__lte=threshold)
    if sku_prefix:
        products = products.filter(sku__startswith=sku_prefix)

    for product in products.order_by("sku").iterator():
        yield _to_row(product)


def fetch_stock(product_ids: Iterable[int]) -> list[ProductStockRow]:
    """Return the current stock for a set of product ids (read-only snapshot).

    Unlike :func:`stream_low_stock` this is a bounded point-in-time read of an
    explicit id set — it backs the bidirectional ``WatchStock`` RPC, which polls
    the current watch set each tick. Unknown ids are simply absent from the
    result (no error), matching the read-only, best-effort nature of a watch.

    Args:
        product_ids: The ids currently subscribed; an empty set yields ``[]``.

    Returns:
        One :class:`ProductStockRow` per existing product, ordered by SKU.
    """
    ids = list(product_ids)
    if not ids:
        return []

    products = Product.objects.filter(pk__in=ids).order_by("sku")
    return [_to_row(product) for product in products]
