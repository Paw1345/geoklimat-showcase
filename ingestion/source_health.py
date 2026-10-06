"""Read-only, safe presentation of recorded source health; never probes HTTP."""

from datetime import timedelta

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from ingestion.models import IngestionRun
from ingestion.sopo_policy import DETAIL as SOPO_DETAIL
from ingestion.sopo_policy import blocked_source as sopo_blocked_source
from ingestion.source_policy import POLICY_ERROR

ERROR_REASONS = {
    "Cannot confirm robots policy": "Nie potwierdzono polityki robots.txt; pobieranie zablokowane.",
    "robots.txt disallows this path": "Polityka robots.txt zabrania pobierania tej ścieżki.",
    "Network connection or timeout failure": "Błąd połączenia lub przekroczenie czasu odpowiedzi.",
    "Remote service unavailable": "Usługa źródłowa jest niedostępna lub ogranicza żądania.",
    "Unexpected HTTP response": "Źródło zwróciło nieoczekiwany status HTTP.",
}


def with_run_summary(sources):
    latest = IngestionRun.objects.filter(source_id=OuterRef("pk")).order_by("-started_at", "-pk")
    return sources.annotate(
        latest_run_at=Subquery(latest.values("started_at")[:1]),
        latest_run_status=Subquery(latest.values("status")[:1]),
        latest_run_error=Subquery(latest.values("error")[:1]),
    )


def is_stale(source, now):
    window = timedelta(minutes=max(1440, source.interval_minutes * 3))
    return source.last_success_at is None or source.last_success_at < now - window


def source_health(source, now=None):
    now = now or timezone.now()
    if sopo_blocked_source(source):
        state, label, detail = "terms_blocked", "Ograniczenie warunków SOPO", SOPO_DETAIL
    elif not source.activation_approved:
        state, label, detail = (
            "unapproved",
            "Brak zatwierdzenia",
            "Kanał nie spełnia warunku aktywacji pobierania.",
        )
    elif not source.enabled:
        state, label, detail = "disabled", "Wyłączony", "Automatyczne pobieranie nie jest włączone."
    elif (
        getattr(source, "latest_run_status", None) == "failed"
        and getattr(source, "latest_run_error", "") == POLICY_ERROR
    ):
        state, label, detail = (
            "policy_changed",
            "Import zatrzymany",
            "Zmieniono zgodę lub profil importu. Pobrana partia nie została zapisana; "
            "nie oznacza to awarii dostawcy. Sprawdź konfigurację przed kolejnym cyklem.",
        )
    elif source.failures:
        reason = "Pobieranie wymaga sprawdzenia przez operatora."
        if getattr(source, "latest_run_status", None) == "failed":
            reason = ERROR_REASONS.get(getattr(source, "latest_run_error", ""), reason)
        state, label, detail = "failed", "Błąd pobierania", reason
    elif source.last_success_at is None:
        state, label, detail = (
            "pending",
            "Brak udanego pobrania",
            "Nie zapisano jeszcze poprawnego cyklu pobierania.",
        )
    elif is_stale(source, now):
        state, label, detail = (
            "stale",
            "Nieaktualne pobieranie",
            "Ostatni sukces przekroczył próg świeżości kanału.",
        )
    elif getattr(source, "latest_run_status", None) == "partial":
        state, label, detail = (
            "partial",
            "Pobranie częściowe",
            "Ostatni cykl odrzucił część wpisów; wymaga sprawdzenia.",
        )
    else:
        state, label, detail = (
            "recent",
            "Ostatnie pobranie poprawne",
            "Nie jest to gwarancja pełnego pokrycia źródła.",
        )
    return {"source": source, "state": state, "label": label, "detail": detail}
