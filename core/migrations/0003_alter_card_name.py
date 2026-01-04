from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_withdrawal_fully_withdrawn"),
    ]

    operations = [
        migrations.AlterField(
            model_name="card",
            name="name",
            field=models.CharField(max_length=120),
        ),
    ]
