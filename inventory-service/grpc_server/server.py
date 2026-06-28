"""Standalone gRPC server entrypoint for the inventory-service.

Run with ``python -m grpc_server.server`` (the container entrypoint does this
after running migrations). This process — not Django's web server — is what the
container runs.

Boot order matters: we call ``django.setup()`` first to load settings and the app
registry so the ORM is usable, and only *then* import the servicer (which
transitively imports models). Importing models before ``django.setup()`` raises
``AppRegistryNotReady``.
"""
import logging
import os
import signal
from concurrent import futures
from types import FrameType

import django
import grpc

logger = logging.getLogger("inventory.grpc")

# Seconds to let in-flight RPCs finish during a graceful shutdown.
_SHUTDOWN_GRACE = 10


def serve() -> None:
    """Bootstrap Django, build the gRPC server, and serve until terminated."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventory_project.settings")
    django.setup()

    # Imported after django.setup() so the app registry / ORM is ready.
    from django.conf import settings

    from generated import order_inventory_pb2_grpc as pb2_grpc
    from grpc_server.servicer import InventoryServicer

    # Sync server: each in-flight RPC runs on a pool thread, where blocking ORM
    # calls (select_for_update, transactions) are fine. (Decision D-021.)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=settings.GRPC_MAX_WORKERS))
    pb2_grpc.add_InventoryServiceServicer_to_server(InventoryServicer(), server)

    address = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(address)  # internal-only network; TLS is a future extension
    server.start()
    logger.info("inventory gRPC server listening on %s", address)

    def _graceful_shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("received signal %s, draining (grace=%ss)", signum, _SHUTDOWN_GRACE)
        # Stop accepting new RPCs; give in-flight calls up to grace seconds.
        server.stop(_SHUTDOWN_GRACE)

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    server.wait_for_termination()
    logger.info("inventory gRPC server stopped")


if __name__ == "__main__":
    serve()
