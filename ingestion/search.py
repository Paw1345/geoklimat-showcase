"""One expression shared by the PostgreSQL GIN index and search queries."""

from django.contrib.postgres.search import SearchVector


def document_vector():
    # 'simple' preserves Polish words and acronyms; no misleading English stemming.
    return SearchVector("title", config="simple", weight="A") + SearchVector(
        "summary", config="simple", weight="B"
    )
