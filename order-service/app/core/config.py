"""Runtime configuration, loaded from environment variables."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings for the order-service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # order-db connection (async SQLAlchemy + asyncpg, D-030)
    postgres_db: str = Field(default="orders", alias="POSTGRES_DB")
    postgres_user: str = Field(default="orders", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="order-db", alias="POSTGRES_HOST")
    postgres_port: str = Field(default="5432", alias="POSTGRES_PORT")

    # gRPC client → inventory-service (comma-separated backend addresses)
    inventory_grpc_targets: str = Field(
        default="inventory-service:50051", alias="INVENTORY_GRPC_TARGETS"
    )
    # Shared secret verified by inventory's auth interceptor (D-034)
    grpc_auth_token: str = Field(default="", alias="GRPC_AUTH_TOKEN")
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
