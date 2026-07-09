"""Reservation business logic (the service layer).

Framework-agnostic: no gRPC/protobuf here. Concurrency safety comes from
``select_for_update()`` (row-level lock) inside ``transaction.atomic()``, so
concurrent orders cannot both read the same quantity and oversell. Reservation
is all-or-nothing: any unsatisfiable line rolls back the whole batch, but a
per-item breakdown is still returned.
"""
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from django.db import transaction

from .models import Product, StockReservation


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
class ProductStockRow:
    """A single product's current stock, streamed by :func:`stream_low_stock`."""

    product_id: int
    sku: str
    name: str
    available_quantity: int


def reserve_stock(order_ref: str, items: Sequence[ReserveItem]) -> ReservationOutcome:
    """Atomically reserve stock for an order, all-or-nothing.

    Each product row is locked, checked, and (if sufficient) decremented with a
    matching ``StockReservation``. If any item fails — unknown product,
    non-positive quantity, or insufficient stock — the transaction rolls back and
    ``success`` is False, while ``results`` still reflects the per-item evaluation.

    Args:
        order_ref: The order-service order id (cross-service reference, no FK).
        items: Line items to reserve. Duplicate ``product_id`` values work because
            stock is decremented as we go.

    Returns:
        A :class:`ReservationOutcome`; when ``success`` is False no rows persist.
    """
    if not items:
        return ReservationOutcome(success=False, results=[])

    outcomes: list[ItemOutcome] = []
    all_ok = True

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
                outcomes.append(ItemOutcome(item.product_id, False, "product not found"))
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
                quantity=item.quantity,
                status=StockReservation.Status.RESERVED,
            )
            outcomes.append(ItemOutcome(item.product_id, True))

        if not all_ok:
            # Roll back the decrements/reservations but keep the per-item outcomes.
            transaction.set_rollback(True)

    return ReservationOutcome(success=all_ok, results=outcomes)


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
    products = Product.objects.filter(available_quantity__lte=threshold)
    if sku_prefix:
        products = products.filter(sku__startswith=sku_prefix)

    for product in products.order_by("sku").iterator():
        yield ProductStockRow(
            product_id=product.pk,
            sku=product.sku,
            name=product.name,
            available_quantity=product.available_quantity,
        )
