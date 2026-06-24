from django.apps import AppConfig


class InventoryAppConfig(AppConfig):
    """App config for the inventory domain (Product, StockReservation)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory_app"
