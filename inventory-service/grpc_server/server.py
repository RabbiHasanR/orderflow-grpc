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
    # Fallback for the pre-``django.setup()`` window; once Django loads, its
    # LOGGING dict (settings.py, spec 013) configures the ``inventory`` loggers.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventory_project.settings")
    django.setup()

    # Imported after django.setup() so the app registry / ORM is ready.
    from django.conf import settings

    from grpc_health.v1 import health, health_pb2, health_pb2_grpc

    from generated import order_inventory_pb2 as pb2
    from generated import order_inventory_pb2_grpc as pb2_grpc
    from grpc_server.interceptors import AuthInterceptor, LoggingInterceptor
    from grpc_server.servicer import InventoryServicer

    # Sync server so blocking ORM calls run on pool threads (D-021). Interceptor
    # order is outermost first: Logging wraps Auth, so Auth-rejected calls are
    # still logged with their status.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=settings.GRPC_MAX_WORKERS),
        interceptors=[LoggingInterceptor(), AuthInterceptor()],
        # Permit the client's 30s keepalive pings (incl. on idle WatchStock streams
        # with no in-flight data). Without this the server's default ping-strike
        # policy would GOAWAY the connection with "too_many_pings".
        options=[
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_ping_interval_without_data_ms", 10000),
        ],
    )
    pb2_grpc.add_InventoryServiceServicer_to_server(InventoryServicer(), server)

    # Standard gRPC health-checking service (grpc.health.v1). The container probe
    # and any future L7 LB ask it "are you serving?" — a real readiness signal the
    # round-robin client can route around, replacing the bare TCP-connect probe.
    # Auth-exempt (see AuthInterceptor). Its own tiny pool serves Check/Watch so a
    # saturated request pool can't stall the probe.
    health_servicer = health.HealthServicer(
        experimental_non_blocking=True,
        experimental_thread_pool=futures.ThreadPoolExecutor(max_workers=1),
    )
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    service_name = pb2.DESCRIPTOR.services_by_name["InventoryService"].full_name
    health_servicer.set(service_name, health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)  # overall server

    address = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(address)  # internal-only network; TLS is a future extension
    server.start()
    logger.info("inventory gRPC server listening on %s (health: %s)", address, service_name)

    def _graceful_shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("received signal %s, draining (grace=%ss)", signum, _SHUTDOWN_GRACE)
        # Flip health to NOT_SERVING first so probes/LBs stop routing here before
        # we stop accepting new RPCs; then give in-flight calls up to grace seconds.
        health_servicer.enter_graceful_shutdown()
        server.stop(_SHUTDOWN_GRACE)

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    server.wait_for_termination()
    logger.info("inventory gRPC server stopped")


if __name__ == "__main__":
    serve()
