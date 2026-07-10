"""FastAPI app — the public REST edge of OrderFlow.

The app is a gRPC *client*: it opens one shared round-robin channel to
inventory-service in the lifespan and exposes it via ``app.state`` for request
handlers. Schema is managed by Alembic (``alembic upgrade head`` in the
entrypoint), not by the app — see decisions.md.
"""
import logging
from contextlib import asynccontextmanager

import grpc
from fastapi import FastAPI, Response, status
from sqlalchemy import text

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
    app.state.channel = channel  # kept for the readiness probe's connectivity check
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
    """Liveness: is the process up? Cheap and dependency-free — never touches the
    DB or gRPC, so a transient dependency blip can't cause a restart loop."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, str]:
    """Readiness: can this replica actually serve? Verifies the order-db is
    reachable and the inventory channel isn't shut down. Returns ``503`` when a
    dependency is down so an orchestrator/LB can route around this replica."""
    checks: dict[str, str] = {}
    ready = True

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        ready = False

    channel = getattr(app.state, "channel", None)
    state = channel.get_state() if channel is not None else None
    if channel is None or state == grpc.ChannelConnectivity.SHUTDOWN:
        checks["inventory_channel"] = "down"
        ready = False
    else:
        # IDLE/CONNECTING are fine — the channel connects lazily on first RPC.
        checks["inventory_channel"] = state.name.lower()

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "degraded", **checks}


app.include_router(api_router)
