import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0014_data_table_kind")]

    operations = [
        migrations.CreateModel(
            name="CollectionAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("attempt_id", models.UUIDField(unique=True)),
                ("batch_id", models.UUIDField(blank=True, null=True)),
                (
                    "origin",
                    models.CharField(
                        choices=[
                            ("unknown", "Unknown"),
                            ("manual", "Manual"),
                            ("worker", "Worker"),
                        ],
                        max_length=8,
                    ),
                ),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField(db_index=True)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                (
                    "interval_minutes",
                    models.PositiveIntegerField(
                        validators=[django.core.validators.MinValueValidator(15)]
                    ),
                ),
                (
                    "profile_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{64}$")],
                    ),
                ),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("stored", "stored"),
                            ("unchanged", "unchanged"),
                            ("failed", "failed"),
                            ("busy", "busy"),
                        ],
                        max_length=10,
                    ),
                ),
                (
                    "reason_code",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("", ""),
                            ("collection_failed", "collection_failed"),
                            ("lock_busy", "lock_busy"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "channel",
                    models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="collection_attempts",
                        to="ingestion.datachannel",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["channel", "started_at"], name="attempt_channel_started")
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("finished_at__gte", models.F("started_at"))),
                        name="attempt_time_order",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("origin__in", ["unknown", "manual", "worker"])),
                        name="attempt_origin",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("origin", "worker"), _negated=True),
                            models.Q(("batch_id__isnull", False), ("due_at__isnull", False)),
                            _connector="OR",
                        ),
                        name="attempt_worker_context",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("interval_minutes__gte", 15)), name="attempt_interval"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("profile_sha256__regex", "^[0-9a-f]{64}$")),
                        name="attempt_profile_hash",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("outcome__in", ["stored", "unchanged"]), ("reason_code", "")),
                            models.Q(("outcome", "failed"), ("reason_code", "collection_failed")),
                            models.Q(("outcome", "busy"), ("reason_code", "lock_busy")),
                            _connector="OR",
                        ),
                        name="attempt_outcome_reason",
                    ),
                ],
            },
        ),
    ]
