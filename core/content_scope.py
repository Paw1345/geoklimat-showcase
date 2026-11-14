"""Record-type filter, independent of editorial relevance. Publications are the default."""

from django import forms
from django.db.models import Q

CATALOGUE_KINDS = (
    "metadata",
    "dataset_collection",
    "dataset_catalogue",
    "service_directory",
    "service_capabilities",
)


DEFAULT_SCOPE = "publications"


class ContentScopeForm(forms.Form):
    record_scope = forms.ChoiceField(
        label="Zakres rekordów",
        required=False,
        choices=[
            ("publications", "Publikacje, bez katalogów danych i usług (domyślnie)"),
            ("all", "Wszystkie rekordy"),
            ("catalogues", "Katalogi danych i usług"),
        ],
    )

    def clean_record_scope(self):
        # A missing or empty choice means the default, so catalogue metadata stays out of the way.
        return self.cleaned_data["record_scope"] or DEFAULT_SCOPE


def filter_content(items, scope):
    scope = scope or DEFAULT_SCOPE
    if scope == "catalogues":
        return items.filter(metadata__record_kind__in=CATALOGUE_KINDS)
    if scope == "publications":
        # Missing keys must remain visible: NOT IN alone drops SQL NULL on JSON extraction.
        return items.filter(
            Q(metadata__record_kind__isnull=True) | ~Q(metadata__record_kind__in=CATALOGUE_KINDS)
        )
    if scope == "all":
        return items
    raise ValueError("Unknown record scope")
