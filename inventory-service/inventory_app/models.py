"""Inventory domain models.

The cross-service link to orders is by reference only
(``StockReservation.order_ref``) — no shared database, no FK across services.
"""
from django.db import models


class Product(models.Model):
    """A stock-keeping item the inventory service can reserve against."""

    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    available_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sku"]

    def __str__(self) -> str:
        return f"{self.sku} ({self.available_quantity} available)"


class StockReservation(models.Model):
    """A quantity of a product held for a specific order.

    Created (status ``RESERVED``) when an order successfully reserves stock;
    flipped to ``RELEASED`` by the ``ReleaseStock`` compensation when order-service
    fails to persist the order, returning the quantity to available stock.
    """

    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        RELEASED = "RELEASED", "Released"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    order_ref = models.CharField(max_length=128, db_index=True)
    # Stable per-attempt key used to make ReserveStock idempotent. A retry with
    # the same key replays the original outcome instead of decrementing again.
    # Empty string means "no key" (single-shot, dedup disabled).
    idempotency_key = models.CharField(
        max_length=128, blank=True, default="", db_index=True
    )
    quantity = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RESERVED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one ACTIVE reservation per (key, product). Scoped to
            # RESERVED so a later release frees the key for a legitimate
            # re-reserve, and to non-empty keys so keyless single-shot
            # reservations never collide. This is the hard backstop against a
            # racing duplicate slipping past the replay check in reserve_stock.
            models.UniqueConstraint(
                fields=["idempotency_key", "product"],
                condition=models.Q(status="RESERVED") & ~models.Q(idempotency_key=""),
                name="uniq_active_reservation_per_key_product",
            )
        ]

    def __str__(self) -> str:
        return f"{self.order_ref} → {self.product.sku} x{self.quantity} [{self.status}]"
