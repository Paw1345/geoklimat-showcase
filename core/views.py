import logging

from django.core.paginator import Paginator
from django.db import DatabaseError, connection
from django.db.models import Case, F, IntegerField, OuterRef, Prefetch, Q, Subquery, Value, When
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.content_scope import ContentScopeForm, filter_content
from ingestion.models import Classification, Item, RegistryEntry, RegistryRelease, Source
from ingestion.taxonomy import MASTER_TOPICS

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    scope_form = ContentScopeForm(request.GET)
    scope_valid = scope_form.is_valid()
    record_scope = scope_form.cleaned_data.get("record_scope", "")
    sources = Source.objects.prefetch_related("topics").all()
    selected = request.GET.get("source", "")
    topic = request.GET.get("topic", "")
    status = request.GET.get("status", "all")
    kind = request.GET.get("kind", "")
    registry_id = request.GET.get("registry", "")
    items = Item.objects.select_related("source", "classification")
    items = filter_content(items, record_scope) if scope_valid else items.none()
    if selected:
        items = items.filter(source__slug=selected)
    if registry_id.isdigit():
        items = items.filter(source__registry_entries__registry_id=int(registry_id))
        if registry_id == "61":
            items = items.filter(classification__kind="Konsultacje")
    if topic:
        items = items.filter(classification__topics__contains=[topic])
    if status in dict(Classification.STATES):
        items = items.filter(
            Q(classification__manual_state=status)
            | Q(classification__manual_state="", classification__auto_state=status)
        )
    elif status == "pending":
        items = items.filter(classification__isnull=True)
    if kind:
        items = items.filter(classification__kind=kind)
    # Preserve the news-only default, but allow relevant legal acts through explicit filters.
    if not selected and not registry_id and not topic and status == "all" and not kind:
        items = items.exclude(source__adapter="eli")
    representative = (
        items.filter(publication_id=OuterRef("publication_id"))
        .annotate(
            primary_rank=Case(
                When(pk=F("publication__primary_item_id"), then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("primary_rank", "source__priority", "pk")
        .values("pk")[:1]
    )
    items = items.filter(Q(publication__isnull=True) | Q(pk=Subquery(representative)))
    page = Paginator(items, 30).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "home.html",
        {
            "sources": sources,
            "items": page,
            "page_query": query.urlencode(),
            "topics": [(slug, value[0]) for slug, value in MASTER_TOPICS.items()],
            "topic": topic,
            "statuses": Classification.STATES,
            "status": status,
            "kinds": Classification.objects.order_by("kind")
            .values_list("kind", flat=True)
            .distinct(),
            "kind": kind,
            "selected": selected,
            "enabled_count": Source.objects.filter(enabled=True).count(),
            "item_count": Item.objects.count(),
            "registry_count": RegistryEntry.objects.count(),
            "registry_id": registry_id,
            "scope_form": scope_form,
            "record_scope": record_scope,
        },
        status=200 if scope_valid else 400,
    )


@require_GET
def item_history(request, pk):
    from ingestion.changes import threads_for_item

    item = get_object_or_404(
        Item.objects.select_related("source", "publication", "classification"), pk=pk
    )
    members = (
        item.publication.items.select_related("source").all() if item.publication_id else [item]
    )
    versions = Paginator(item.versions.all(), 20).get_page(request.GET.get("page"))
    return render(
        request,
        "history.html",
        {
            "item": item,
            "members": members,
            "versions": versions,
            "legal_links": item.legal_links.select_related("process", "item"),
            "change_threads": threads_for_item(item),
        },
    )


@require_GET
def live(request):
    return JsonResponse({"status": "ok"})


@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.warning("Database readiness check failed")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def registry(request):
    entries = RegistryEntry.objects.prefetch_related("sources").all()
    query = request.GET.get("q", "").strip()[:200]
    if query:
        entries = entries.filter(Q(organization__icontains=query) | Q(name__icontains=query))
    for field in ("priority", "country", "level", "technical_status", "integration_state"):
        value = request.GET.get(field)
        if value:
            entries = entries.filter(**{field: value})
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "registry.html",
        {
            "entries": Paginator(entries, 30).get_page(request.GET.get("page")),
            "filters": [
                {
                    "name": field,
                    "selected": request.GET.get(field, ""),
                    "values": RegistryEntry.objects.order_by(field)
                    .values_list(field, flat=True)
                    .distinct(),
                }
                for field in (
                    "priority",
                    "country",
                    "level",
                    "technical_status",
                    "integration_state",
                )
            ],
            "params": params.urlencode(),
            "total": RegistryEntry.objects.count(),
            "release": RegistryRelease.objects.order_by("-imported_at").first(),
        },
    )


@require_GET
def registry_detail(request, pk):
    from ingestion.gios_catalogue import catalogue_for_registry
    from ingestion.source_health import source_health, with_run_summary

    entry = get_object_or_404(
        RegistryEntry.objects.prefetch_related(
            Prefetch("sources", queryset=with_run_summary(Source.objects.all()))
        ),
        registry_id=pk,
    )
    return render(
        request,
        "registry_detail.html",
        {
            "entry": entry,
            "gios_catalogue": catalogue_for_registry(entry.registry_id),
            "source_health": [source_health(source) for source in entry.sources.all()],
        },
    )


@require_GET
def registry_sheet(request):
    release = RegistryRelease.objects.order_by("-imported_at").first()
    if not release:
        raise Http404
    name = request.GET.get("sheet", "Summary")
    if name not in release.sheets:
        name = "Summary"
    return render(
        request,
        "registry_sheet.html",
        {
            "release": release,
            "name": name,
            "rows": release.sheets[name],
            "names": release.sheets.keys(),
        },
    )
