from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import F, Max, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from ingestion.legislation import current_events
from ingestion.models import LegalEvent, LegalProcess, LegalRelation


@require_GET
def catalog(request):
    query = request.GET.get("q", "").strip()[:200]
    jurisdiction = request.GET.get("jurisdiction", "")
    processes = LegalProcess.objects.annotate(latest=Max("documents__item__published_at")).order_by(
        F("latest").desc(nulls_last=True), "-pk"
    )
    if query:
        processes = processes.filter(Q(identifier__icontains=query) | Q(title__icontains=query))
    if jurisdiction in {"PL", "EU"}:
        processes = processes.filter(jurisdiction=jurisdiction)
    return render(
        request,
        "legal_catalog.html",
        {
            "processes": Paginator(processes, 30).get_page(request.GET.get("page")),
            "q": query,
            "jurisdiction": jurisdiction,
        },
    )


@require_GET
def detail(request, pk):
    process = get_object_or_404(LegalProcess, pk=pk)
    documents = process.documents.select_related("item", "item__source").order_by(
        "item_id", "origin"
    )
    events = current_events(
        LegalEvent.objects.filter(link__process=process).select_related("link__item")
    )
    dates = {}
    for event in events:
        if event.kind != "stage":
            dates.setdefault(event.kind, set()).add(event.date)
    conflicts = [dict(LegalEvent.KINDS)[key] for key, values in dates.items() if len(values) > 1]
    relations = LegalRelation.objects.filter(
        Q(link__process=process) | Q(target=process),
        superseded_at__isnull=True,
        item_hash=F("link__item__content_hash"),
    ).select_related("link__process", "link__item", "target")
    history = (
        LegalEvent.objects.filter(link__process=process)
        .select_related("link__item")
        .order_by("-observed_at", "-pk")
    )
    return render(
        request,
        "legal_detail.html",
        {
            "process": process,
            "documents": documents,
            "events": events,
            "relations": relations,
            "conflicts": conflicts,
            "history": Paginator(history, 30).get_page(request.GET.get("page")),
        },
    )


@require_GET
def deadlines(request):
    today = timezone.localdate()
    mode = request.GET.get("window", "upcoming")
    kinds = ["effective", "consultation_end", "reporting_deadline"]
    events = current_events(
        LegalEvent.objects.filter(kind__in=kinds).select_related("link__item", "link__process")
    )
    if mode == "past":
        events = events.filter(date__lt=today, date__gte=today - timedelta(days=30)).order_by(
            "-date", "pk"
        )
    else:
        mode = "upcoming"
        events = events.filter(date__gte=today, date__lte=today + timedelta(days=90))
    return render(
        request,
        "legal_deadlines.html",
        {
            "events": Paginator(events, 30).get_page(request.GET.get("page")),
            "window": mode,
            "today": today,
        },
    )
