"""Reservation business logic (the service layer).

This module is deliberately framework-agnostic: it knows nothing about gRPC or
protobuf. The gRPC servicer translates protobuf messages into the plain
dataclasses below, calls :func:`reserve_stock`, and translates the result back.
That keeps this logic unit-testable without standing up a gRPC server.

Concurrency is the whole point of this service. Two orders can hit the inventory
backend at the same time; without coordination they could both read the same
``available_quantity`` and both "succeed", overselling the stock. We prevent that
with ``select_for_update()`` (a row-level lock) inside a single
``transaction.atomic()`` block: the second transaction blocks until the first
commits or rolls back, so it sees the up-to-date quantity.

Reservation is **all-or-nothing**: if any line item cannot be satisfied, the whole
batch is rolled back and nothing is reserved — but we still report a per-item
breakdown so the caller knows exactly which item failed and why.
"""
from collections.abc import Sequence
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


def reserve_stock(order_ref: str, items: Sequence[ReserveItem]) -> ReservationOutcome:
    """Atomically reserve stock for an order, all-or-nothing.

    For each item the product row is locked, checked, and (if sufficient)
    decremented with a matching ``StockReservation`` row created. If *any* item
    cannot be satisfied — unknown product, non-positive quantity, or insufficient
    stock — the entire transaction is rolled back and ``success`` is False, while
    ``results`` still reflects the per-item evaluation.

    Args:
        order_ref: The order-service order id this reservation is for. Stored on
            each ``StockReservation`` as the cross-service reference (no FK across
            services).
        items: The line items to reserve. Duplicate ``product_id`` values are
            handled correctly because stock is decremented as we go, so a later
            line sees the reduced balance.

    Returns:
        A :class:`ReservationOutcome`. When ``success`` is False, no rows were
        persisted (the transaction was rolled back).
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
                # Row-level lock: blocks concurrent reservations of the same
                # product until this transaction completes.
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
            # Roll back every decrement and reservation created above, but keep
            # the computed per-item outcomes for the caller. Atomicity without
            # losing the diagnostic detail.
            transaction.set_rollback(True)

    return ReservationOutcome(success=all_ok, results=outcomes)
