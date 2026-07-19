# Generated for GIM Connect inactivity tracking and chat replies.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("connect", "0003_pushdevice"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_active_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="message",
            name="reply_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="connect.message",
            ),
        ),
    ]
