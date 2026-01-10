from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_fix_transaction_fk"),
    ]

    operations = [
        migrations.CreateModel(
            name="BankColor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bank", models.CharField(max_length=80, unique=True)),
                ("color", models.CharField(default="#000000", max_length=7)),
            ],
        ),
    ]
