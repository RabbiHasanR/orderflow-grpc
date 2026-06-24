"""Django settings for the inventory-service.

Django is used here as an ORM + migration system only — there is no web server,
no templates, and no contrib apps beyond what the ORM needs. The runtime is a
standalone gRPC server (added in a later phase) that calls ``django.setup()``.

All secrets and environment-specific values come from environment variables;
nothing sensitive is hardcoded.
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
