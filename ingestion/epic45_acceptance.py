"""K6 acceptance ledger: only explicit operator decisions, each with dated evidence.

Configuration, green tests or a card proposal never add an entry here. A decision
recorded below does not change the database; ``epic45_integration_state`` writes the
registry state separately, dry-run by default.
"""

from dataclasses import dataclass

from django.db import transaction

from ingestion.models import RegistryEntry
from ingestion.transport import FetchError

ACCEPTED_WITH_EXCEPTIONS = "accepted_with_exceptions"
NOT_REVIEWED = "not_reviewed"
GROUP_A_EVIDENCE = "docs/EPIC45-K6-DATA-CARDS-2026-10-14.md (Grupa A); memory-bank/changelog.md"
GROUP_A_DECIDED_AT = "2026-10-14T20:25:59Z"


@dataclass(frozen=True)
class Decision:
    status: str
    decided_at: str
    evidence: str
    exceptions: str
    from_state: str = "needs_connector"
    target_state: str = "data_configured"


IMGW_EVIDENCE = "docs/EPIC45-K6-ACCEPTANCE-2026-10-15.md (R033/R034); docs/IMGW-WARNINGS.md"
IMGW_DECIDED_AT = "2026-10-15T12:12:58Z"


def _imgw(exceptions):
    # Seeded as data_configured in 2026-09; acceptance needs no registry write.
    return Decision(
        ACCEPTED_WITH_EXCEPTIONS,
        IMGW_DECIDED_AT,
        IMGW_EVIDENCE,
        exceptions,
        from_state="data_configured",
    )


def _group_a(exceptions):
    return Decision(ACCEPTED_WITH_EXCEPTIONS, GROUP_A_DECIDED_AT, GROUP_A_EVIDENCE, exceptions)


SPATIAL_EVIDENCE = "docs/EPIC45-K6-CARDS-2026-10-21.md (R039/R053); memory-bank/changelog.md"
# Time the written decision was read in the session; the chat message carries no timestamp.
SPATIAL_DECIDED_AT = "2026-10-21T20:48:56Z"


def _spatial(exceptions, from_state="needs_connector"):
    return Decision(
        ACCEPTED_WITH_EXCEPTIONS,
        SPATIAL_DECIDED_AT,
        SPATIAL_EVIDENCE,
        exceptions,
        from_state=from_state,
    )


DECISIONS = {
    33: _imgw("pomiary co 60 zamiast 30 min; ciągłość 30 dni to osobna bramka K4"),
    34: _imgw(
        "pomiary co 60 zamiast 30 min; kształt listy ostrzeżeń meteo UNCONFIRMED do "
        "pierwszego ostrzeżenia; ciągłość 30 dni to osobna bramka K4"
    ),
    15: _group_a("interwał 1440 zamiast 360 min"),
    36: _group_a("interwał 30 dni; tylko profil otworów"),
    50: _group_a("jedna wersja tabel"),
    63: _group_a("interwał 1440 zamiast 360 min"),
    64: _group_a("tylko warstwa hałd"),
    65: _group_a("interwał 1440 zamiast 360 min"),
    200: _group_a("tylko pas PL (workbook: UE); tylko CDI"),
    202: _group_a("tylko pas PL (workbook: UE); tylko FWI; interwał 1440 zamiast 120 min"),
    39: _spatial(
        "wydzielenia bez geometrii (skipGeometry, tylko atrybuty); leśnictwa: 1 brak "
        "geometrii (id 2292) i 2290 rekordów bez licznika (unknown); nadleśnictwa: "
        "geometria unknown; skany co 30 dni zamiast 360 min; spójność migawki dostawcy "
        "niezweryfikowana"
    ),
    53: _spatial(
        "dane obiektów tylko z nazw geograficznych; pozostałe 44 usługi WFS tylko "
        "GetCapabilities (bez GetFeature); interwał 1440 zamiast 360 min; kompletność "
        "względem dostawcy UNCONFIRMED",
        # The WFS service catalogue made 53 configured before acceptance.
        from_state="configured",
    ),
}


def decision(registry_id):
    return DECISIONS.get(registry_id)


def note(registry_id, item):
    return (
        f"K6 {item.status} {item.decided_at} [USER]; odstępstwa: {item.exceptions}. "
        f"Dowód: {item.evidence}. Nie zamyka K3/K4/K5 ani pełnego 4.5 (R{registry_id:03})."
    )


def state_plan(registry_ids=None):
    """Compare recorded decisions with the registry; unexpected states are never overwritten."""
    wanted = sorted(DECISIONS if registry_ids is None else registry_ids)
    unknown = [value for value in wanted if value not in DECISIONS]
    if unknown:
        raise FetchError(f"No recorded K6 decision for registry IDs: {unknown}")
    entries = {
        entry.registry_id: entry
        for entry in RegistryEntry.objects.filter(registry_id__in=wanted).order_by("registry_id")
    }
    missing = [value for value in wanted if value not in entries]
    if missing:
        raise FetchError(f"Registry entries missing: {missing}")
    plan = {"pending": [], "already": [], "conflicts": []}
    for registry_id in wanted:
        entry, item = entries[registry_id], DECISIONS[registry_id]
        row = {
            "registry_id": registry_id,
            "current_state": entry.integration_state,
            "target_state": item.target_state,
            "decided_at": item.decided_at,
        }
        if entry.integration_state == item.target_state:
            plan["already"].append(row)
        elif entry.integration_state == item.from_state:
            plan["pending"].append(row)
        else:
            plan["conflicts"].append(row)
    return plan, entries


def apply_states(registry_ids=None):
    """Write target states for pending decisions in one transaction; refuse on any conflict."""
    with transaction.atomic():
        ids = sorted(DECISIONS if registry_ids is None else registry_ids)
        list(RegistryEntry.objects.select_for_update().filter(registry_id__in=ids))
        plan, entries = state_plan(ids)
        if plan["conflicts"]:
            raise FetchError("Unexpected registry state; nothing written")
        for row in plan["pending"]:
            entry = entries[row["registry_id"]]
            previous = entry.integration_note.strip()
            entry.integration_state = row["target_state"]
            entry.integration_note = note(entry.registry_id, DECISIONS[entry.registry_id]) + (
                f" Poprzednia notatka: {previous}" if previous else ""
            )
            entry.save(update_fields=["integration_state", "integration_note"])
    return plan
