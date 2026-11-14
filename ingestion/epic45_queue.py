"""Read-only operational triage; audit hints never prove current access or intent."""

from collections import Counter
from datetime import timedelta

from django.utils import timezone

from ingestion.models import RegistryEntry, Source


def audit_hint(audit):
    error = str(audit.get("error", "")).casefold()
    for phrase, code in [
        ("robots.txt disallows", "robots_disallow"),
        ("cannot confirm robots", "robots_unconfirmed"),
        ("redirect", "redirect_review"),
        ("timeout", "network_or_timeout"),
        ("network", "network_or_timeout"),
        ("parse", "parser_review"),
        ("malformed", "parser_review"),
    ]:
        if phrase in error:
            return code
    return "unclassified" if error else "no_recorded_error"


def operational_queue(now=None):
    now = now or timezone.now()
    rows = []
    sources = Source.objects.prefetch_related("registry_entries").order_by("slug")
    for source in sources:
        stale = bool(
            source.last_success_at
            and source.last_success_at
            < now - timedelta(minutes=max(1440, 3 * source.interval_minutes))
        )
        never_succeeded = source.last_success_at is None
        if source.enabled and not source.failures and not stale and not never_succeeded:
            continue
        hint = audit_hint(source.last_audit)
        audit_status = source.last_audit.get("status")
        audit_status = audit_status if audit_status in {"verified", "unverified"} else "unknown"
        next_step = (
            "Review current failed/stale channel; approved access and equivalent scope required"
            if source.enabled
            else "Review why verified channel is disabled; no automatic enable"
            if audit_status == "verified"
            else "Review access/endpoint/parser; fresh scoped audit before activation"
        )
        rows.append(
            {
                "slug": source.slug,
                "registry_ids": sorted(
                    entry.registry_id for entry in source.registry_entries.all()
                ),
                "owner_subepic": "4.5.2",
                "enabled": source.enabled,
                "activation_approved": source.activation_approved,
                "audit_status_recorded": audit_status,
                "audit_problem_hint": hint,
                "audited_at": source.audited_at,
                "last_success_at": source.last_success_at,
                "failures": source.failures,
                "stale": stale,
                "never_succeeded": never_succeeded,
                "disabled_reason_confirmed": False,
                "next_step": next_step,
            }
        )
    regional = list(
        RegistryEntry.objects.filter(integration_state="needs_endpoint")
        .order_by("registry_id")
        .values("registry_id", "organization", "name", "priority")
    )
    return {
        "as_of": now,
        "channel_rows": rows,
        "endpoint_rows": regional,
        "disabled_count": sum(not row["enabled"] for row in rows),
        "enabled_problem_count": sum(row["enabled"] for row in rows),
        "audit_hints": dict(Counter(row["audit_problem_hint"] for row in rows)),
        "http_requests": False,
        "database_writes": False,
        "automatic_activation": False,
        "counts_overlap": True,
    }
