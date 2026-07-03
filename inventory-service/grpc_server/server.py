"""Standalone gRPC server entrypoint (``python -m grpc_server.server``).

``django.setup()` runs before importing the servicer so the app registry / ORM
is ready; importing models earlier raises ``AppRegistryNotReady``.
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
    from grpc_server.interceptors import AuthInterceptor, LoggingInterceptor
    from grpc_server.servicer import InventoryServicer

    # Sync server so blocking ORM calls run on pool threads (D-021). Interceptor
    # order is outermost first: Logging wraps Auth, so Auth-rejected calls are
    # still logged with their status.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=settings.GRPC_MAX_WORKERS),
        interceptors=[LoggingInterceptor(), AuthInterceptor()],
    )
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
