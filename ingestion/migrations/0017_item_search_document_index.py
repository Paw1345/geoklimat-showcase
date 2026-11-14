# S9b 2026-10-16: build the new GIN index without blocking reads or writes, then
# drop the old expression index. CONCURRENTLY cannot run inside a transaction.

import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("ingestion", "0016_item_search_document"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="item",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_document"], name="item_search_document_gin"
            ),
        ),
        RemoveIndexConcurrently(
            model_name="item",
            name="item_document_search_gin",
        ),
    ]
