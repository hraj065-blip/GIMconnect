# Generated for GIM Connect mobile push device registration.

import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("connect", "0002_alter_revealrequest_status_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PushDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=512, unique=True)),
                ("platform", models.CharField(choices=[("android", "Android")], default="android", max_length=20)),
                ("device_id", models.CharField(blank=True, max_length=128)),
                ("app_version", models.CharField(blank=True, max_length=40)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_devices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="pushdevice",
            index=models.Index(fields=["user", "is_active"], name="connect_pus_user_id_c3fda1_idx"),
        ),
        migrations.AddIndex(
            model_name="pushdevice",
            index=models.Index(fields=["token"], name="connect_pus_token_34581a_idx"),
        ),
    ]
