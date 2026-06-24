"""Data migration: seed a few sample products so the service has stock to reserve.

Runs as part of `migrate`, so a freshly-booted inventory-service already has
demonstrable inventory. Reversible: unseeding deletes exactly these SKUs.
"""
from django.db import migrations

# (sku, name, available_quantity)
SEED_PRODUCTS = [
    ("SKU-WIDGET", "Standard Widget", 100),
    ("SKU-GADGET", "Premium Gadget", 50),
    ("SKU-GIZMO", "Limited Gizmo", 10),
]


def seed_products(apps, schema_editor):
    """Insert sample products (idempotent via get_or_create on sku)."""
    Product = apps.get_model("inventory_app", "Product")
    for sku, name, quantity in SEED_PRODUCTS:
        Product.objects.get_or_create(
            sku=sku,
            defaults={"name": name, "available_quantity": quantity},
        )


def unseed_products(apps, schema_editor):
    """Remove the seeded products on rollback."""
    Product = apps.get_model("inventory_app", "Product")
    Product.objects.filter(sku__in=[sku for sku, _, _ in SEED_PRODUCTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory_app", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_products, unseed_products),
    ]
