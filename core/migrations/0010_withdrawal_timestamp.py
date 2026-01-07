from datetime import datetime, time

from django.db import migrations, models
from django.utils import timezone


def forwards(apps, schema_editor):
    Withdrawal = apps.get_model("core", "Withdrawal")
    for wd in Withdrawal.objects.filter(timestamp__isnull=True):
        base = datetime.combine(wd.date, time.min)
        wd.timestamp = timezone.make_aware(base)
        wd.save(update_fields=["timestamp"])


def backwards(apps, schema_editor):
    Withdrawal = apps.get_model("core", "Withdrawal")
    Withdrawal.objects.update(timestamp=None)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_add_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="withdrawal",
            name="timestamp",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(forwards, backwards),
    ]
