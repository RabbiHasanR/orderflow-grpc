"""Pydantic schemas for the inventory read endpoints."""
from typing import Literal

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


class WatchCommandIn(BaseModel):
    """One command a WebSocket client sends on the /stock-watch stream.

    Validated straight from the socket's inbound JSON, then mapped to a protobuf
    ``WatchCommand`` before it goes onto the gRPC request stream.
    """

    action: Literal["subscribe", "unsubscribe"]
    product_ids: list[int]


class StockUpdateOut(BaseModel):
    """One live stock update pushed to the WebSocket client.

    ``from_attributes`` builds it directly from the protobuf ``StockUpdate``; the
    enum ``kind`` is emitted as its integer value, so we translate it to a label
    in the bridge for a friendlier payload.
    """

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    sku: str
    name: str
    available_quantity: int
    kind: Literal["snapshot", "changed"]
