"""Emit a Postgres NOTIFY whenever a product's available_quantity changes.

This is the write-side half of the push-based ``WatchStock`` (spec 008, D-039).
An ``AFTER UPDATE`` trigger calls ``pg_notify('stock_changed', <json>)`` so every
open watch — on any replica — is told the moment stock moves, instead of polling.

Living in the DB (not app code) means the notification fires for *every* writer:
``reserve_stock``, a future restock endpoint, even a manual ``UPDATE`` — the DB is
the source of truth. The ``WHEN`` guard skips no-op updates that don't actually
change the quantity.
"""
from django.db import migrations

NOTIFY_CHANNEL = "stock_changed"

FORWARD_SQL = """
CREATE OR REPLACE FUNCTION notify_stock_change() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'stock_changed',
    json_build_object(
      'product_id', NEW.id,
      'available_quantity', NEW.available_quantity
    )::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER stock_changed_notify
  AFTER UPDATE OF available_quantity ON inventory_app_product
  FOR EACH ROW
  WHEN (OLD.available_quantity IS DISTINCT FROM NEW.available_quantity)
  EXECUTE FUNCTION notify_stock_change();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS stock_changed_notify ON inventory_app_product;
DROP FUNCTION IF EXISTS notify_stock_change();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("inventory_app", "0002_seed_products"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
