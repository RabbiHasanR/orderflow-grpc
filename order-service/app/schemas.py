"""Pydantic request/response schemas for the REST edge.

Validation happens here, at the boundary, *before* any gRPC or DB work — a
malformed body is rejected with ``422`` locally and never reaches the network hop
(see workflow.md step 2). ``product_id``/``quantity`` are constrained positive so
the cheap, obvious rejections don't cost a round-trip.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderItemIn(BaseModel):
    """One requested line in an incoming order."""

    product_id: int = Field(gt=0, description="Inventory product id (positive).")
    quantity: int = Field(gt=0, description="Requested amount (positive).")


class OrderCreate(BaseModel):
    """Request body for ``POST /orders``."""

    items: list[OrderItemIn] = Field(min_length=1, description="At least one line.")


class OrderItemOut(BaseModel):
    """A persisted order line in a response."""

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    quantity: int


class OrderOut(BaseModel):
    """A persisted order returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    created_at: datetime
    items: list[OrderItemOut]


class ItemResultOut(BaseModel):
    """Per-item reservation outcome, surfaced in the ``409`` failure body."""

    product_id: int
    reserved: bool
    reason: str
