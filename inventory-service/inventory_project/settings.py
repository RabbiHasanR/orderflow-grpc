"""Django settings for the inventory-service (ORM + migrations only, no web server).

The runtime is a standalone gRPC server that calls ``django.setup()``. All
secrets and environment-specific values come from environment variables.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from the environment (``1/true/yes/on`` are truthy)."""
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- Core ----------------------------------------------------------------------

# Dev-only fallback; in any real environment DJANGO_SECRET_KEY must be set.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Only our app is installed — no auth/admin/sessions/contenttypes, because this
# service never handles HTTP and only needs its own tables.
INSTALLED_APPS = [
    "inventory_app",
]

# Kept minimal; unused at runtime (no web server) but referenced by Django checks.
ROOT_URLCONF = "inventory_project.urls"

# --- Database ------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "inventory"),
        "USER": os.environ.get("POSTGRES_USER", "inventory"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "inventory-db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging -------------------------------------------------------------------

# Single source of truth for level + format (spec 013). Mirrors order-service's
# format so a combined `docker compose logs` reads consistently across services.
# ``serve()`` still calls ``basicConfig`` as a guard for the pre-``django.setup()``
# window; once Django is configured this dict wins. Two namespaces:
#   inventory.grpc    — transport / stream lifecycle (interceptors + servicer)
#   inventory.service — domain events (service layer)
# Later (spec 010) the console formatter swaps to structured JSON — a config
# change here, not a code change at the call sites.
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "loggers": {
        "inventory": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
    },
}

# --- I18N / TZ -----------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- gRPC (consumed by the server/interceptor phases) --------------------------

# Auth token the client must present; the server-side auth interceptor checks it.
GRPC_AUTH_TOKEN = os.environ.get("GRPC_AUTH_TOKEN", "")
GRPC_PORT = os.environ.get("GRPC_PORT", "50051")
GRPC_MAX_WORKERS = int(os.environ.get("GRPC_MAX_WORKERS", "10"))
# WatchStock (bidirectional) is push-based: updates come from Postgres NOTIFY,
# not a poll (spec 008, D-039). This is only how often the helper threads wake to
# re-check for shutdown while blocked — a responsiveness knob, not a data poll.
GRPC_WATCH_TICK_SECONDS = float(os.environ.get("GRPC_WATCH_TICK_SECONDS", "1.0"))
