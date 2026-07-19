# Generated for GIM Connect anonymous pre-reveal persona fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("connect", "0004_last_active_and_message_reply"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="mockup_fun_name",
            field=models.CharField(
                blank=True,
                help_text="Optional anonymous display name shown before identity reveal.",
                max_length=48,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="anonymous_intro",
            field=models.TextField(
                blank=True,
                help_text="Optional short anonymous intro shown before identity reveal. Maximum 60 words.",
            ),
        ),
    ]
