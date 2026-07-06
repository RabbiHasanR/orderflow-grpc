"""Aggregates all module routers into one router the app includes.

Mounted at the app root (not ``/api/v1``) to preserve the existing public
contract: ``POST /orders`` / ``GET /orders/{id}`` (spec 002). Register new
module routers here as the service grows.
"""
from fastapi import APIRouter

from app.modules.orders.router import router as orders_router

api_router = APIRouter()
api_router.include_router(orders_router, prefix="/orders", tags=["Orders"])
