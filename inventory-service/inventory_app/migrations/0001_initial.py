"""Initial schema: Product and StockReservation.

Hand-written to match inventory_app/models.py (Django is not installed locally;
the service runs in Docker). If you later change the models, prefer regenerating
with `python manage.py makemigrations` inside the container.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sku", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("available_quantity", models.PositiveIntegerField(default=0)),
            ],
            options={
                "ordering": ["sku"],
            },
        ),
        migrations.CreateModel(
            name="StockReservation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("order_ref", models.CharField(db_index=True, max_length=128)),
                ("quantity", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[("RESERVED", "Reserved"), ("RELEASED", "Released")],
                        default="RESERVED",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reservations",
                        to="inventory_app.product",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
