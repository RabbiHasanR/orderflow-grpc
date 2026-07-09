"""Shared FastAPI dependency for the inventory gRPC client.

The client is a single process-wide object opened in the app lifespan
(``main.py``) and stashed on ``app.state``. Every module that needs it resolves
it through this one dependency so the wiring lives in exactly one place.
"""
from fastapi import Request

from app.grpc_client.client import InventoryClient


def get_inventory(request: Request) -> InventoryClient:
    """Return the process-wide inventory gRPC client opened in the lifespan."""
    return request.app.state.inventory
