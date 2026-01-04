from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_card_unique_identity"),
    ]

    operations = [
        migrations.CreateModel(
            name="CardGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True)),
            ],
        ),
        migrations.AddField(
            model_name="card",
            name="group",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="cards", to="core.cardgroup"),
        ),
    ]
