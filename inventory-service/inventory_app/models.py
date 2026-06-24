"""Inventory domain models.

These map directly onto the inventory-service's own PostgreSQL database. The
cross-service link to orders is **by reference only** (``StockReservation.order_ref``
holds the order-service's order id) — there is no shared database and no foreign
key across services. Consistency is coordinated over gRPC, not by the database.
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

    Created (status ``RESERVED``) when an order successfully reserves stock; a
    future phase may flip it to ``RELEASED`` to support rollback.
    """

    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        RELEASED = "RELEASED", "Released"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    # The order-service order id — the cross-service reference (no FK across services).
    order_ref = models.CharField(max_length=128, db_index=True)
    quantity = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RESERVED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.order_ref} → {self.product.sku} x{self.quantity} [{self.status}]"
