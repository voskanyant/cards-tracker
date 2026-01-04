from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_alter_card_name"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="card",
            constraint=models.UniqueConstraint(
                fields=("name", "bank", "card_number"),
                name="unique_card_identity",
            ),
        ),
    ]
