"""SQLAlchemy models for order-service (its own order-db).

``OrderItem.product_id`` references inventory's ``Product.id`` by value only —
no cross-service foreign key, no shared database.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _new_order_id() -> str:
    """Generate the order id used as the cross-service ``order_ref``."""
    return str(uuid4())


class Order(Base):
    """A customer order. Its id doubles as the ``order_ref`` sent to inventory."""

    __tablename__ = "orders"

    # Generated up front so it can be passed as order_ref before the RPC.
    id: Mapped[str] = mapped_column(primary_key=True, default=_new_order_id)
    # status kept for future saga/outbox work (PENDING → CONFIRMED/CANCELLED).
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
    product_id: Mapped[int] = mapped_column()  # inventory's Product.id, no cross-DB FK
    quantity: Mapped[int] = mapped_column()

    order: Mapped["Order"] = relationship(back_populates="items")
