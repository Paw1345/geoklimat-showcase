from django import forms
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.paginator import Paginator
from django.db.models import F, FloatField, OuterRef, Q, Subquery, Value
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.content_scope import ContentScopeForm, filter_content
from ingestion.models import Classification, Item, RegistryEntry, Source
from ingestion.search import document_vector
from ingestion.taxonomy import MASTER_TOPICS


class SearchForm(ContentScopeForm):
    q = forms.CharField(label="Szukaj w tytule i opisie", max_length=200, required=False)
    source = forms.ChoiceField(label="Źródło", required=False)
    registry = forms.IntegerField(
        label="ID rejestru", min_value=1, max_value=2147483647, required=False
    )
    topic = forms.ChoiceField(label="Temat", required=False)
    status = forms.ChoiceField(
        label="Ocena",
        required=False,
        choices=[
            ("", "Wszystkie oceny"),
            *Classification.STATES,
            ("pending", "Niesklasyfikowane"),
        ],
    )
    kind = forms.ChoiceField(label="Rodzaj informacji", required=False)
    language = forms.ChoiceField(label="Język", required=False)
    date_from = forms.DateField(
        label="Publikacja od", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label="Publikacja do", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    sort = forms.ChoiceField(
        label="Kolejność",
        required=False,
        choices=[
            ("relevance", "Dopasowanie"),
            ("newest", "Najnowsza publikacja"),
            ("oldest", "Najstarsza publikacja"),
            ("collected", "Ostatnio pobrane"),
        ],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].choices = [
            ("", "Wszystkie źródła"),
            *Source.objects.values_list("slug", "name"),
        ]
        self.fields["topic"].choices = [
            ("", "Wszystkie tematy"),
            *[(k, v[0]) for k, v in MASTER_TOPICS.items()],
        ]
        self.fields["kind"].choices = [
            ("", "Wszystkie rodzaje"),
            *[
                (v, v)
                for v in Classification.objects.order_by("kind")
                .values_list("kind", flat=True)
                .distinct()
            ],
        ]
        self.fields["language"].choices = [
            ("", "Wszystkie języki"),
            *[
                (v, v.upper())
                for v in Source.objects.order_by("language")
                .values_list("language", flat=True)
                .distinct()
            ],
        ]

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise forms.ValidationError("Data początkowa nie może być późniejsza niż końcowa.")
        if (
            data.get("registry")
            and not RegistryEntry.objects.filter(registry_id=data["registry"]).exists()
        ):
            self.add_error("registry", "Nie ma takiej pozycji w rejestrze.")
        return data


def search_items(data):
    items = Item.objects.select_related("source", "classification")
    items = filter_content(items, data.get("record_scope", ""))
    query = data.get("q", "")
    if query:
        terms = SearchQuery(query, config="simple", search_type="websearch")
        items = (
            items.annotate(document=document_vector())
            .filter(document=terms)
            .annotate(rank=SearchRank(document_vector(), terms))
        )
    else:
        items = items.annotate(rank=Value(0.0, output_field=FloatField()))
    for field, lookup in (
        ("source", "source__slug"),
        ("language", "source__language"),
        ("kind", "classification__kind"),
    ):
        if data.get(field):
            items = items.filter(**{lookup: data[field]})
    if data.get("registry"):
        items = items.filter(source__registry_entries__registry_id=data["registry"])
        if data["registry"] == 61:
            items = items.filter(classification__kind="Konsultacje")
    if data.get("topic"):
        items = items.filter(classification__topics__contains=[data["topic"]])
    status = data.get("status")
    if status in dict(Classification.STATES):
        items = items.filter(
            Q(classification__manual_state=status)
            | Q(classification__manual_state="", classification__auto_state=status)
        )
    elif status == "pending":
        items = items.filter(classification__isnull=True)
    if data.get("date_from"):
        items = items.filter(published_at__date__gte=data["date_from"])
    if data.get("date_to"):
        items = items.filter(published_at__date__lte=data["date_to"])
    # Choose only among matching members. A nonmatching primary must not hide a hit.
    member = (
        items.filter(publication_id=OuterRef("publication_id"))
        .order_by("-rank", "source__priority", "pk")
        .values("pk")[:1]
    )
    items = items.filter(Q(publication__isnull=True) | Q(pk=Subquery(member)))
    sort = data.get("sort") or "relevance"
    orders = {
        "newest": [F("published_at").desc(nulls_last=True), "-pk"],
        "oldest": [F("published_at").asc(nulls_last=True), "pk"],
        "collected": ["-first_seen_at", "-pk"],
        "relevance": ["-rank", "-first_seen_at", "-pk"],
    }
    return items.order_by(*orders[sort])


@require_GET
def search(request):
    form = SearchForm(request.GET)
    valid = form.is_valid()
    results = search_items(form.cleaned_data) if valid else Item.objects.none()
    page = Paginator(results, 30).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "search.html",
        {"form": form, "items": page, "page_query": params.urlencode()},
        status=200 if valid else 400,
    )
