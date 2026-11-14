# S9b 2026-10-16: stored search vector. Adding a stored generated column rewrites
# ingestion_item under an ACCESS EXCLUSIVE lock (local: ~3.3 s for 30k rows / 25 MB).
# Run it during a deploy, while web and worker are restarting.

import django.contrib.postgres.search
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ingestion", "0015_collection_attempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="search_document",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.CombinedSearchVector(
                    django.contrib.postgres.search.SearchVector(
                        "title", config="simple", weight="A"
                    ),
                    "||",
                    django.contrib.postgres.search.SearchVector(
                        "summary", config="simple", weight="B"
                    ),
                    django.contrib.postgres.search.SearchConfig("simple"),
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
    ]
