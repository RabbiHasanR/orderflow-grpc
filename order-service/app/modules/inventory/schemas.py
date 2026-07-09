"""Pydantic schemas for the inventory read endpoints."""
from pydantic import BaseModel, ConfigDict


class ProductStockOut(BaseModel):
    """One product's current stock, streamed as an NDJSON line by /low-stock.

    ``from_attributes`` lets us build it straight from the protobuf ``ProductStock``
    message (attribute access), keeping the router a thin mapper.
    """

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    sku: str
    name: str
    available_quantity: int
