"""Optional record-type filters, independent of editorial relevance."""

from django import forms
from django.db.models import Q

CATALOGUE_KINDS = (
    "metadata",
    "dataset_collection",
    "dataset_catalogue",
    "service_directory",
    "service_capabilities",
)


class ContentScopeForm(forms.Form):
    record_scope = forms.ChoiceField(
        label="Zakres rekordów",
        required=False,
        choices=[
            ("", "Wszystkie rekordy"),
            ("publications", "Publikacje (bez katalogów danych i usług)"),
            ("catalogues", "Katalogi danych i usług"),
        ],
    )


def filter_content(items, scope):
    if scope == "catalogues":
        return items.filter(metadata__record_kind__in=CATALOGUE_KINDS)
    if scope == "publications":
        # Missing keys must remain visible: NOT IN alone drops SQL NULL on JSON extraction.
        return items.filter(
            Q(metadata__record_kind__isnull=True) | ~Q(metadata__record_kind__in=CATALOGUE_KINDS)
        )
    if not scope:
        return items
    raise ValueError("Unknown record scope")
