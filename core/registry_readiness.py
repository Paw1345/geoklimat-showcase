"""Read-only operational evidence across the registry; not business sign-off."""

from collections import Counter
from datetime import timedelta

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import OuterRef, Prefetch, Subquery
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.models import DataChannel, DataHarvest, DownloadJob, RegistryEntry, Source
from ingestion.source_health import source_health, with_run_summary

OPEN_STATES = {"needs_connector", "needs_endpoint", "requires_access"}


class Filters(forms.Form):
    q = forms.CharField(required=False, max_length=200)
    priority = forms.ChoiceField(
        required=False, choices=[("", "Wszystkie"), *[(p, p) for p in ["P0", "P1", "P2", "P3"]]]
    )
    attention = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Wszystkie"),
            ("yes", "Wymagające przeglądu"),
            ("no", "Bez flag technicznych"),
        ],
    )
    format = forms.ChoiceField(required=False, choices=[("", "HTML"), ("json", "JSON")])


def data_evidence(channel, now):
    base = {
        "id": channel.pk,
        "name": channel.name,
        "kind": channel.kind,
        "enabled": channel.enabled,
        "approved": channel.access_approved,
        "last_status": channel.last_status,
        "last_checked_at": channel.checked_at.isoformat() if channel.checked_at else None,
        "harvest_status": channel.harvest_status,
        "harvest_records": channel.harvest_records,
        "download_status": channel.download_status,
    }
    if not channel.access_approved:
        state = "unapproved"
    elif channel.kind == "file":
        # No job is expected until a human requests a download.
        state = (
            "on_demand"
            if channel.download_status in {None, "complete", "expired", "superseded"}
            else (
                "download_attention"
                if channel.download_status in {"blocked", "failed", "retry"}
                else "downloading"
            )
        )
    elif not channel.enabled:
        state = "disabled"
    elif channel.harvest_status in {"blocked", "failed"}:
        state = "failed"
    elif channel.last_status not in {
        "success",
        "stored",
        "verified",
        "unchanged",
        "not_modified",
        "collecting",
        "complete",
    }:
        state = "pending_or_failed"
    elif not channel.checked_at or channel.checked_at < now - timedelta(
        minutes=max(1440, channel.interval_minutes * 3)
    ):
        state = "stale"
    else:
        state = "recent"
    return {**base, "state": state}


def report(now=None):
    now = now or timezone.now()
    harvest = DataHarvest.objects.filter(channel_id=OuterRef("pk")).order_by("-started_at", "-pk")
    download = DownloadJob.objects.filter(channel_id=OuterRef("pk")).order_by("-created_at", "-pk")
    channels = DataChannel.objects.only(
        "pk",
        "registry_id",
        "name",
        "kind",
        "enabled",
        "access_approved",
        "last_status",
        "checked_at",
        "interval_minutes",
    ).annotate(
        harvest_status=Subquery(harvest.values("status")[:1]),
        harvest_records=Subquery(harvest.values("records")[:1]),
        download_status=Subquery(download.values("status")[:1]),
    )
    sources = with_run_summary(
        Source.objects.only(
            "pk",
            "name",
            "enabled",
            "url",
            "config",
            "activation_approved",
            "failures",
            "last_success_at",
            "interval_minutes",
            "scope",
        )
    )
    entries = RegistryEntry.objects.only(
        "pk",
        "registry_id",
        "name",
        "organization",
        "priority",
        "integration_state",
    ).prefetch_related(
        Prefetch("sources", queryset=sources), Prefetch("data_channels", queryset=channels)
    )
    rows = []
    for entry in entries:
        news = []
        for source in entry.sources.all():
            health = source_health(source, now)
            news.append(
                {
                    "id": source.pk,
                    "name": source.name,
                    "state": health["state"],
                    "label": health["label"],
                    "detail": health["detail"],
                    "scope": source.scope,
                    "enabled": source.enabled,
                    "approved": source.activation_approved,
                    "last_success_at": source.last_success_at.isoformat()
                    if source.last_success_at
                    else None,
                }
            )
        data = [data_evidence(channel, now) for channel in entry.data_channels.all()]
        reasons = []
        if entry.integration_state in OPEN_STATES:
            reasons.append("Niezamknięty stan integracji w rejestrze")
        if not news and not data:
            reasons.append("Brak powiązanego kanału")
        if any(s["state"] != "recent" for s in news):
            reasons.append("Kanał informacyjny wymaga przeglądu")
        if any(c["state"] not in {"recent", "on_demand", "downloading"} for c in data):
            reasons.append("Kanał danych wymaga przeglądu")
        rows.append(
            {
                "registry_id": entry.registry_id,
                "organization": entry.organization,
                "name": entry.name,
                "priority": entry.priority,
                "integration_state": entry.integration_state,
                "sources": news,
                "data": data,
                "attention": bool(reasons),
                "reasons": reasons,
                "business_coverage_verified": False,
            }
        )
    unique_sources = {s["id"]: s for row in rows for s in row["sources"]}
    unique_data = {c["id"]: c for row in rows for c in row["data"]}
    return {
        "generated_at": now.isoformat(),
        "registry_total": len(rows),
        "source_total": len(unique_sources),
        "source_active": sum(s["enabled"] for s in unique_sources.values()),
        "source_states": dict(Counter(s["state"] for s in unique_sources.values())),
        "data_total": len(unique_data),
        "data_states": dict(Counter(c["state"] for c in unique_data.values())),
        "attention_total": sum(r["attention"] for r in rows),
        "integration_states": dict(Counter(r["integration_state"] for r in rows)),
        "business_signoff_included": False,
        "rows": rows,
    }


@require_GET
@staff_member_required(login_url="login")
@never_cache
def dashboard(request):
    form = Filters(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Niepoprawne filtry raportu.")
    result = filtered_report(report(), form.cleaned_data)
    if form.cleaned_data["format"] == "json":
        return JsonResponse(result)
    return render(request, "registry_readiness.html", {"report": result, "form": form})


def filtered_report(result, values):
    rows = result["rows"]
    if values["q"]:
        query = values["q"].casefold()
        rows = [
            r
            for r in rows
            if query
            in (r["organization"] + " " + r["name"] + " " + str(r["registry_id"])).casefold()
        ]
    if values["priority"]:
        rows = [r for r in rows if r["priority"] == values["priority"]]
    if values["attention"]:
        rows = [r for r in rows if r["attention"] == (values["attention"] == "yes")]
    result["rows"] = rows
    result["filtered_total"] = len(rows)
    return result
