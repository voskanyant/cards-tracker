from django.db import migrations, connections


def rebuild_transaction_table(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys=OFF;")
        cursor.execute("ALTER TABLE core_transaction RENAME TO core_transaction_tmp;")
        cursor.execute(
            """
            CREATE TABLE core_transaction (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                created_at datetime NOT NULL,
                timestamp datetime NOT NULL,
                amount_rub decimal NOT NULL,
                amount_usd decimal NOT NULL,
                rate decimal NULL,
                notes text NOT NULL,
                card_id bigint NOT NULL REFERENCES core_card(id) DEFERRABLE INITIALLY DEFERRED,
                client_id bigint NOT NULL REFERENCES core_client(id) DEFERRABLE INITIALLY DEFERRED
            );
            """
        )
        cursor.execute(
            """
            INSERT INTO core_transaction (
                id, created_at, timestamp, amount_rub, amount_usd, rate, notes, card_id, client_id
            )
            SELECT
                id, created_at, timestamp, amount_rub, amount_usd, rate, notes, card_id, client_id
            FROM core_transaction_tmp;
            """
        )
        cursor.execute("DROP TABLE core_transaction_tmp;")
        cursor.execute("PRAGMA foreign_keys=ON;")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_alter_card_group"),
    ]

    operations = [
        migrations.RunPython(rebuild_transaction_table, reverse_code=migrations.RunPython.noop),
    ]
