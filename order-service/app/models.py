"""SQLAlchemy models for order-service (its own order-db).

The link to inventory's products is **by reference only** — ``OrderItem.product_id``
holds inventory's ``Product.id``, but there is no foreign key across services and
no shared database. Consistency is coordinated over gRPC (the reservation result),
not by the database. This is the core distributed-systems lesson of the project.

An order is persisted **only after** its stock reservation succeeds (see
architecture.md's happy path), so a persisted row is effectively ``CONFIRMED``.
The ``status`` column is kept for the future saga/outbox work (``PENDING`` →
``CONFIRMED``/``CANCELLED``) that would close the orphaned-reservation gap.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _new_order_id() -> str:
    """Generate the order id used as the cross-service ``order_ref``."""
    return str(uuid4())


class Order(Base):
    """A customer order. Its id doubles as the ``order_ref`` sent to inventory."""

    __tablename__ = "orders"

    # Generated up front (UUID) so it can be passed as order_ref *before* the
    # reservation RPC — inventory stores it on each StockReservation.
    id: Mapped[str] = mapped_column(primary_key=True, default=_new_order_id)
    status: Mapped[str] = mapped_column(default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OrderItem(Base):
    """One line of an order: ``quantity`` of inventory product ``product_id``."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    # Inventory's Product.id — cross-service reference, no FK across databases.
    product_id: Mapped[int] = mapped_column()
    quantity: Mapped[int] = mapped_column()

    order: Mapped["Order"] = relationship(back_populates="items")
