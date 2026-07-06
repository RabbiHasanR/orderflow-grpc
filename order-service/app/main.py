"""FastAPI app — the public REST edge of OrderFlow.

The app is a gRPC *client*: it opens one shared round-robin channel to
inventory-service in the lifespan and exposes it via ``app.state`` for request
handlers. Schema is managed by Alembic (``alembic upgrade head`` in the
entrypoint), not by the app — see decisions.md.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.api_v1 import api_router
from app.core.config import get_settings
from app.core.database import engine
from app.grpc_client.client import InventoryClient, build_channel

logger = logging.getLogger("order.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the shared gRPC channel on startup; tear it and the DB engine down on exit."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()

    channel = build_channel(settings)
    app.state.inventory = InventoryClient(channel, settings.grpc_deadline_seconds)
    logger.info("order-service ready")
    try:
        yield
    finally:
        await channel.close()
        await engine.dispose()


app = FastAPI(title="OrderFlow — order-service", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe for compose ``depends_on`` / the container healthcheck."""
    return {"status": "ok"}


app.include_router(api_router)
