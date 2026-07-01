"""Runtime configuration for the order-service.

All values come from environment variables (nothing sensitive is hardcoded),
loaded once into a cached :class:`Settings` singleton. This mirrors the
inventory-service convention of reading config from the environment, but uses
``pydantic-settings`` since we already depend on Pydantic for request validation.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings for the order-service.

    Attributes are populated from environment variables of the same (upper-cased)
    name. Only ``POSTGRES_PASSWORD`` and ``GRPC_AUTH_TOKEN`` are truly secret;
    the rest have safe local defaults.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- order-db connection (async SQLAlchemy + asyncpg, decision D-030) -------
    postgres_db: str = Field(default="orders", alias="POSTGRES_DB")
    postgres_user: str = Field(default="orders", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="order-db", alias="POSTGRES_HOST")
    postgres_port: str = Field(default="5432", alias="POSTGRES_PORT")

    # --- gRPC client → inventory-service ---------------------------------------
    # Comma-separated list of backend addresses. One today (single replica,
    # D-026); adding a 2nd entry engages the round_robin policy with no code
    # change (client-side load balancing, project goal #3).
    inventory_grpc_targets: str = Field(
        default="inventory-service:50051", alias="INVENTORY_GRPC_TARGETS"
    )
    # Shared secret the client attaches as call metadata; the inventory server's
    # auth interceptor verifies it (decision D-034).
    grpc_auth_token: str = Field(default="", alias="GRPC_AUTH_TOKEN")
    # Per-call deadline (seconds) for the ReserveStock RPC → DEADLINE_EXCEEDED.
    grpc_deadline_seconds: float = Field(default=5.0, alias="GRPC_DEADLINE_SECONDS")

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for order-db (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def inventory_targets(self) -> list[str]:
        """Parsed, non-empty inventory backend addresses."""
        return [t.strip() for t in self.inventory_grpc_targets.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()
