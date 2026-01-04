from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_alter_card_group"),
    ]

    operations = [
        migrations.AlterField(
            model_name="card",
            name="group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cards",
                to="core.cardgroup",
            ),
        ),
    ]
