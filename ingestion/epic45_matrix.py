"""Read-only acceptance inventory; only the K6 ledger records acceptance, never configuration."""

import html
from collections import Counter

from django.db.models import Prefetch
from django.utils import timezone

from ingestion.epic45_acceptance import NOT_REVIEWED, decision
from ingestion.models import DataChannel, RegistryEntry, Source
from ingestion.transport import FetchError

OWNERS = {
    "4.5.2": {97, 98, 104, 125, 131, 132, 134, 136, 168},
    "4.5.3": {6, 46, 47, 48, 49, 50},
    "4.5.4": {15, 16, 39, 41},
    "4.5.5": {36, 37, 62, 63, 64, 65},
    "4.5.6": {180, 181, 186, 189},
    "4.5.7": {192, 196, 200, 202, 203, 204},
}
ACCESS_DECISIONS = {29, 43, 56, 169, 193, 194}


def owner(entry):
    for package, ids in OWNERS.items():
        if entry.registry_id in ids:
            return package
    if entry.integration_state in {"needs_connector", "needs_endpoint"}:
        raise FetchError("Unassigned registry gap in acceptance matrix")
    return "4.5.11" if entry.registry_id in ACCESS_DECISIONS else "4.5.12"


def inventory(expected_count=219):
    entries = list(
        RegistryEntry.objects.select_related("release")
        .prefetch_related(
            Prefetch("sources", queryset=Source.objects.order_by("slug")),
            Prefetch("data_channels", queryset=DataChannel.objects.order_by("slug")),
        )
        .order_by("registry_id")
    )
    if len(entries) != expected_count:
        raise FetchError("Registry count differs from expected acceptance baseline")
    if expected_count == 219 and [entry.registry_id for entry in entries] != list(range(1, 220)):
        raise FetchError("Registry identities differ from expected acceptance baseline")
    rows = []
    for entry in entries:
        sources, channels = list(entry.sources.all()), list(entry.data_channels.all())
        accepted = decision(entry.registry_id)
        rows.append(
            {
                "registry_id": entry.registry_id,
                "organization": entry.organization,
                "name": entry.name,
                "priority": entry.priority,
                "workbook_sha256": entry.release.sha256,
                "expected_scope_raw": entry.master.get("Scope"),
                "expected_topics_raw": entry.master.get("Topics"),
                "expected_region_raw": entry.master.get("Country/Area"),
                "expected_poll_minutes_raw": entry.master.get("Poll interval (min)"),
                "product_selection": "requires_scope_review",
                "collection_mode": "requires_product_review",
                "retention_days": 30,
                "history_availability": "unconfirmed",
                "integration_state": entry.integration_state,
                "owner_subepic": owner(entry),
                "access_wait_previously_agreed": entry.registry_id in ACCESS_DECISIONS,
                "acceptance_status": accepted.status if accepted else NOT_REVIEWED,
                "acceptance_decided_at": accepted.decided_at if accepted else None,
                "acceptance_exceptions": accepted.exceptions if accepted else None,
                "acceptance_evidence": accepted.evidence if accepted else None,
                "integration_state_pending": bool(
                    accepted
                    and entry.integration_state == accepted.from_state
                    and accepted.target_state != entry.integration_state
                ),
                "delivered_information_channels": [
                    {
                        "slug": source.slug,
                        "scope": source.scope,
                        "enabled": source.enabled,
                        "approved": source.activation_approved,
                        "last_success_at": source.last_success_at,
                        "failures": source.failures,
                        "poll_minutes": source.interval_minutes,
                    }
                    for source in sources
                ],
                "delivered_data_channels": [
                    {
                        "slug": channel.slug,
                        "name": channel.name,
                        "kind": channel.kind,
                        "region": channel.region,
                        "enabled": channel.enabled,
                        "approved": channel.access_approved,
                        "last_status": channel.last_status,
                        "checked_at": channel.checked_at,
                        "poll_minutes": channel.interval_minutes,
                    }
                    for channel in channels
                ],
                "next_step": (
                    "Accepted with listed exceptions; K3/K4/K5 gates stay separate"
                    if accepted
                    else "Define product/scope/evidence; review access wait"
                    if entry.registry_id in ACCESS_DECISIONS
                    else "Compare expected/delivered products; collect acceptance evidence"
                ),
            }
        )
    return {
        "as_of": timezone.now(),
        "expected_entries": expected_count,
        "entries": rows,
        "states": dict(Counter(row["integration_state"] for row in rows)),
        "owners": dict(Counter(row["owner_subepic"] for row in rows)),
        "acceptance_reviewed_count": sum(row["acceptance_status"] != NOT_REVIEWED for row in rows),
        "acceptance": dict(Counter(row["acceptance_status"] for row in rows)),
        "scope_approved": False,
        "http_requests": False,
        "database_writes": False,
        "payload_integrity_checked": False,
    }


RESULT_BYTES = 2 * 1024 * 1024


def cell(value):
    text = "UNCONFIRMED" if value is None or value == "" else str(value)
    text = html.escape(text, quote=True)
    for char in ("\\", "|", "[", "]", "*", "_", "`"):
        text = text.replace(char, "\\" + char)
    return text.replace("\r", " ").replace("\n", " / ")


def _active(item, approved):
    return "on" if item["enabled"] and item[approved] else "off"


def _acceptance(row):
    if row["acceptance_status"] == NOT_REVIEWED:
        return "do przeglądu"
    pending = "; stan rejestru do zapisu" if row["integration_state_pending"] else ""
    return (
        f"{row['acceptance_status']} {row['acceptance_decided_at']}; odstępstwa: "
        f"{cell(row['acceptance_exceptions'])}; dowód: {cell(row['acceptance_evidence'])}{pending}"
    )


def markdown(report):
    rows = report["entries"]
    pending = [row["registry_id"] for row in rows if row["integration_state_pending"]]
    lines = [
        "# K6 — macierz odbioru 219 pozycji z dowodami",
        "",
        f"Odczyt: {report['as_of'].isoformat()}. Pozycje: {len(rows)}. Rejestr SHA256: "
        + ", ".join(sorted({row["workbook_sha256"] for row in rows}))
        + ".",
        "",
        "Wygenerowano `epic45_matrix --format markdown` w transakcji READ ONLY, bez HTTP i "
        "zapisów. Odbiór pochodzi wyłącznie z rejestru decyzji K6 "
        "(`ingestion/epic45_acceptance.py`); konfiguracja, sukces kanału i testy nie są "
        "odbiorem. Retencja 30 dni; historia i kompletność względem dostawcy UNCONFIRMED, "
        "o ile karta nie mówi inaczej. Zakres całego 4.5 nie jest zatwierdzony.",
        "",
        "Stany rejestru: "
        + ", ".join(f"{key} {value}" for key, value in sorted(report["states"].items()))
        + ".",
        "Odbiór: "
        + ", ".join(f"{key} {value}" for key, value in sorted(report["acceptance"].items()))
        + ".",
        "Odebrane, ale stan rejestru jeszcze niezapisany: "
        + (", ".join(str(value) for value in pending) or "brak")
        + ".",
        "",
        "| ID | Instytucja / pozycja | Wymagany zakres — workbook | Obszar / interwał | "
        "Pakiet / stan | Kanały informacyjne | Kanały danych | Odbiór i dowód |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        information = "; ".join(
            f"{cell(item['slug'])} ({_active(item, 'approved')})"
            for item in row["delivered_information_channels"]
        )
        data = "; ".join(
            f"{cell(item['slug'])} ({item['kind']}/{cell(item['last_status'])}/"
            f"{_active(item, 'approved')})"
            for item in row["delivered_data_channels"]
        )
        lines.append(
            f"| {row['registry_id']} | {cell(row['organization'])} — {cell(row['name'])} | "
            f"{cell(row['expected_scope_raw'])} | {cell(row['expected_region_raw'])} / "
            f"{cell(row['expected_poll_minutes_raw'])} min | {row['owner_subepic']} / "
            f"{row['integration_state']} | {information or 'brak'} | {data or 'brak'} | "
            f"{_acceptance(row)} |"
        )
    value = "\n".join(lines) + "\n"
    if len(value.encode()) > RESULT_BYTES:
        raise FetchError("Acceptance matrix output budget exceeded")
    return value
