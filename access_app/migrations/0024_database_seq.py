from django.db import migrations, models
from django.db.models import F


def backfill_seq(apps, schema_editor):
    Database = apps.get_model("access_app", "Database")
    Database.objects.update(seq=F("id"))


class Migration(migrations.Migration):

    dependencies = [
        ("access_app", "0023_systemsetting_wa_lid_map_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="database",
            name="seq",
            field=models.FloatField(
                db_index=True, default=0, editable=False, verbose_name="الترتيب"
            ),
        ),
        migrations.RunPython(backfill_seq, migrations.RunPython.noop),
    ]
