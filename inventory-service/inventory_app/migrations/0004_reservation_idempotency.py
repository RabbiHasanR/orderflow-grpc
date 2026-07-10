"""Make ReserveStock idempotent: add a per-attempt key + a partial unique index.

``idempotency_key`` lets a retried reservation (same key) replay the original
outcome instead of decrementing stock twice — the fix for a client retry after a
timeout overselling (Phase 2a of the production plan).

The ``UniqueConstraint`` is a *partial* index (scoped to RESERVED, non-empty keys)
so it is the hard backstop against a racing duplicate that slips past the replay
check, while still allowing a released key to be reserved again.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory_app", "0003_stock_change_notify"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockreservation",
            name="idempotency_key",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=128
            ),
        ),
        migrations.AddConstraint(
            model_name="stockreservation",
            constraint=models.UniqueConstraint(
                fields=["idempotency_key", "product"],
                condition=models.Q(status="RESERVED") & ~models.Q(idempotency_key=""),
                name="uniq_active_reservation_per_key_product",
            ),
        ),
    ]
