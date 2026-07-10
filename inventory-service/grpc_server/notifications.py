"""Postgres LISTEN/NOTIFY plumbing for the push-based WatchStock (spec 008, D-039).

The read-side counterpart to migration ``0003_stock_change_notify``: that trigger
NOTIFYs on the ``stock_changed`` channel; here we LISTEN on it and turn each
notification into a :class:`StockEvent` the servicer can act on.

Why a *raw* psycopg connection (not the Django ORM): LISTEN needs a long-lived
connection that blocks waiting for notifications, which would tie up and confuse
Django's thread-local connection handling. Keeping it separate means the ORM
(``fetch_stock``) and the listener never fight over the same connection.
"""
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
from django.conf import settings

logger = logging.getLogger("inventory.grpc")

NOTIFY_CHANNEL = "stock_changed"
# Seconds to wait after a dropped LISTEN connection before reconnecting.
_RECONNECT_BACKOFF = 1.0


@dataclass(frozen=True)
class StockEvent:
    """One thing the listener learned. ``kind`` is either:

    * ``"resync"`` — a fresh LISTEN connection was (re)established; the caller
      should re-read its whole watch set, since any NOTIFY sent while we were
      disconnected was missed. ``product_id`` is ``None``.
    * ``"changed"`` — product ``product_id``'s stock changed; the caller should
      re-read that product and emit an update if it still cares about it.
    """

    kind: str
    product_id: int | None = None


def _connect() -> psycopg.Connection:
    """Open an autocommit psycopg connection from Django's DB settings.

    Autocommit is required for LISTEN/NOTIFY — notifications are only delivered
    outside an open transaction.
    """
    db = settings.DATABASES["default"]
    return psycopg.connect(
        dbname=db["NAME"],
        user=db["USER"],
        password=db["PASSWORD"],
        host=db["HOST"],
        port=db["PORT"],
        autocommit=True,
    )


def listen_stock_changes(
    stop: threading.Event, tick_seconds: float
) -> Iterator[StockEvent]:
    """Yield :class:`StockEvent`s from the ``stock_changed`` channel until ``stop``.

    Reconnects automatically if the connection drops, yielding a fresh ``resync``
    event each time it comes back so the caller can recover missed notifications.
    ``tick_seconds`` bounds how long we block in ``notifies`` before re-checking
    ``stop`` — it is a shutdown-responsiveness knob, not a poll interval.
    """
    while not stop.is_set():
        conn = None
        try:
            conn = _connect()
            conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
            logger.info("WatchStock listener connected, LISTEN %s", NOTIFY_CHANNEL)
            # A fresh connection may have missed notifications while down — tell
            # the caller to re-read everything it watches.
            yield StockEvent(kind="resync")

            while not stop.is_set():
                for notify in conn.notifies(timeout=tick_seconds):
                    if stop.is_set():
                        break
                    product_id = _parse_product_id(notify.payload)
                    if product_id is not None:
                        yield StockEvent(kind="changed", product_id=product_id)
        except psycopg.Error as exc:
            logger.warning("WatchStock listener connection lost: %s", exc)
            # Wait (interruptibly) before reconnecting; the outer loop retries.
            stop.wait(_RECONNECT_BACKOFF)
        finally:
            if conn is not None:
                conn.close()


def _parse_product_id(payload: str) -> int | None:
    """Extract ``product_id`` from a NOTIFY payload; ``None`` if malformed."""
    try:
        return int(json.loads(payload)["product_id"])
    except (ValueError, KeyError, TypeError):
        logger.warning("WatchStock: unparseable notify payload %r", payload)
        return None
