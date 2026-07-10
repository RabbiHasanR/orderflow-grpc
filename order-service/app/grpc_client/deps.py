"""Shared FastAPI dependency for the inventory gRPC client.

The client is a single process-wide object opened in the app lifespan
(``main.py``) and stashed on ``app.state``. Every module that needs it resolves
it through this one dependency so the wiring lives in exactly one place.

Typed as ``HTTPConnection`` (the common base of both ``Request`` and
``WebSocket``) so the *same* dependency serves the REST endpoints and the
``/stock-watch`` WebSocket — a WebSocket route has no ``Request`` to inject.
"""
from starlette.requests import HTTPConnection

from app.grpc_client.client import InventoryClient


def get_inventory(connection: HTTPConnection) -> InventoryClient:
    """Return the process-wide inventory gRPC client opened in the lifespan."""
    return connection.app.state.inventory
