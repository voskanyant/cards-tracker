from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_fix_transaction_fk"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(fields=["-timestamp"], name="core_trans_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(fields=["card", "-timestamp"], name="core_trans_card_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(fields=["client", "-timestamp"], name="core_trans_client_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="withdrawal",
            index=models.Index(fields=["date"], name="core_withdraw_date_idx"),
        ),
        migrations.AddIndex(
            model_name="withdrawal",
            index=models.Index(fields=["card", "date"], name="core_withdraw_card_date_idx"),
        ),
    ]
