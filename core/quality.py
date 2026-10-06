from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, F
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.description_quality import MISSING_DESCRIPTION
from ingestion.models import Item, RegistryEntry, Source, ThreadImpact
from ingestion.source_health import is_stale


def report():
    now = timezone.now()
    active = Source.objects.filter(enabled=True)
    stale = sum(
        1 for source in active.only("last_success_at", "interval_minutes") if is_stale(source, now)
    )
    return {
        "generated_at": now.isoformat(),
        "sources_total": Source.objects.count(),
        "sources_active": active.count(),
        "sources_with_failures": active.filter(failures__gt=0).count(),
        "sources_stale": stale,
        "sources_missing_reuse_notes": active.filter(reuse_notes="").count(),
        "items_total": Item.objects.count(),
        "items_without_description": Item.objects.filter(
            summary__regex=MISSING_DESCRIPTION
        ).count(),
        "items_without_publication_date": Item.objects.filter(published_at__isnull=True).count(),
        "items_without_classification": Item.objects.filter(classification__isnull=True).count(),
        "impacts_changed_source": ThreadImpact.objects.filter(superseded_at__isnull=True)
        .exclude(item_hash=F("item__content_hash"))
        .count(),
        "registry_states": dict(
            RegistryEntry.objects.values("integration_state")
            .annotate(total=Count("pk"))
            .values_list("integration_state", "total")
        ),
    }


@require_GET
@staff_member_required(login_url="login")
@never_cache
def dashboard(request):
    data = report()
    if request.GET.get("format") == "json":
        return JsonResponse(data)
    return render(request, "quality.html", {"report": data})


@require_GET
def information(request):
    return render(request, "information.html")
