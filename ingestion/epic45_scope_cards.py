"""Scope-review cards from one local read-only snapshot, never product acceptance."""

from collections import Counter

from django.db import connection, transaction

from core.registry_readiness import report as readiness_report
from ingestion.epic45_matrix import cell, inventory
from ingestion.transport import FetchError

RESULT_BYTES = 2 * 1024 * 1024
REVIEW_FIELDS = (
    "product_layer_operation",
    "geographic_mask_crs",
    "historical_period",
    "units_source_dates",
    "licence_access",
    "collection_mode_budget",
    "completeness_denominator",
    "import_repeat_export_evidence",
)


def build_cards(matrix, readiness):
    entries = matrix["entries"]
    technical = {row["registry_id"]: row for row in readiness["rows"]}
    ids = [entry["registry_id"] for entry in entries]
    if (
        len(ids) != len(set(ids))
        or len(technical) != len(readiness["rows"])
        or set(ids) != set(technical)
    ):
        raise FetchError("Scope cards and readiness identities differ")
    cards = []
    for entry in entries:
        row = technical[entry["registry_id"]]
        tasks = [
            {
                "id": f"R{entry['registry_id']:03}-SCOPE",
                "stage": "K0",
                "owner": "assistant_and_operator",
                "state": "requires_scope_review",
                "action": "Nazwij produkty i okresy; porównaj wymagania z konfiguracją.",
            }
        ]
        if entry["integration_state"] in {"needs_endpoint", "needs_connector"}:
            tasks.append(
                {
                    "id": f"R{entry['registry_id']:03}-INTEGRATION",
                    "stage": "K2",
                    "owner": "assistant",
                    "state": "to_implement_or_verify",
                    "action": (
                        "Potwierdź dozwolony adres tematyczny przed implementacją."
                        if entry["integration_state"] == "needs_endpoint"
                        else "Dostarcz brakujące produkty; katalog nie zastępuje pełnych danych."
                    ),
                }
            )
        if row["attention"]:
            tasks.append(
                {
                    "id": f"R{entry['registry_id']:03}-TECHNICAL",
                    "stage": "K1" if entry["integration_state"] == "requires_access" else "K2",
                    "owner": "assistant",
                    "state": "requires_technical_review",
                    "action": "; ".join(row["reasons"]),
                }
            )
        if entry["registry_id"] == 62:
            tasks.append(
                {
                    "id": "R062-PROVIDER",
                    "stage": "K1",
                    "owner": "operator_and_provider",
                    "state": "blocked",
                    "action": "SOPO wstrzymany: wyjaśnienie z dostawcą, bez lokalnego obejścia.",
                }
            )
        if entry["registry_id"] == 196:
            tasks.append(
                {
                    "id": "R196-TERMS",
                    "stage": "K1",
                    "owner": "operator",
                    "state": "requires_terms_review",
                    "action": "Sprawdź zgodę EMS przed zapisem; to nie akceptacja warunków.",
                }
            )
        if entry["registry_id"] == 172:
            tasks.append(
                {
                    "id": "R172-SKIP",
                    "stage": "K1",
                    "owner": "operator",
                    "state": "previously_skipped",
                    "action": "Zachowaj pominięcie OEIL; nie oznacza odbioru brakującego zakresu.",
                }
            )
        cards.append(
            {
                **entry,
                "card_id": f"R{entry['registry_id']:03}",
                "technical_attention": row["attention"],
                "technical_reasons": list(row["reasons"]),
                "information_states": [
                    {
                        key: source[key]
                        for key in (
                            "id",
                            "name",
                            "state",
                            "enabled",
                            "approved",
                            "scope",
                            "last_success_at",
                        )
                    }
                    for source in row["sources"]
                ],
                "data_states": [
                    {
                        key: channel[key]
                        for key in (
                            "id",
                            "name",
                            "kind",
                            "state",
                            "approved",
                            "harvest_status",
                            "download_status",
                        )
                    }
                    for channel in row["data"]
                ],
                "unconfirmed_contract_fields": list(REVIEW_FIELDS),
                "end_condition": (
                    "Named product; permitted access; agreed completeness; real import/repeat/"
                    "export/retention evidence; explicit operator acceptance or scoped exception."
                ),
                "decision_owner": "operator_with_assistant_proposal",
                "work_items": tasks,
            }
        )
    return {
        "as_of": matrix["as_of"],
        "registry_total": len(cards),
        "states": matrix["states"],
        "owners": matrix["owners"],
        "attention_total": sum(card["technical_attention"] for card in cards),
        "work_item_states": dict(
            Counter(task["state"] for card in cards for task in card["work_items"])
        ),
        "acceptance_reviewed_count": matrix["acceptance_reviewed_count"],
        "scope_approved": False,
        "http_requests": False,
        "database_writes": False,
        "payload_integrity_checked": False,
        "cards": cards,
    }


def scope_cards(expected_count=219):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        matrix = inventory(expected_count)
        readiness = readiness_report(now=matrix["as_of"])
        return build_cards(matrix, readiness)


def markdown(report):
    lines = [
        "# K0 — karty zakresu wszystkich pozycji epika 4.5",
        "",
        f"Snapshot: {report['as_of'].isoformat()}. Karty: {report['registry_total']}.",
        "Rejestr SHA256: "
        + ", ".join(sorted({card["workbook_sha256"] for card in report["cards"]}))
        + ".",
        "",
        "Odczyt lokalnej bazy, bez HTTP/zapisów. NIE odbiór, kompletność ani akceptacja warunków.",
        "Zakres źródłowy to wymaganie; konfiguracja i sukces nie dowodzą pełnego produktu.",
        "Wszystkie karty: retencja30dni; historia/produkt/tryb do przeglądu; odbiór not_reviewed.",
        "Pola do uzgodnienia: " + ", ".join(REVIEW_FIELDS) + ".",
        "Właściciel decyzji produktu: operator z propozycją asystenta. Nie nadpisujemy workbook.",
        "Koniec: produkt, dostęp, kompletność, import/powtórzenie/eksport/retencja, odbiór.",
        "Wymagane zadania: SCOPE dla każdej pozycji; INTEGRATION dla luk; TECHNICAL dla flag.",
        "Reaudyt dostawcy/licencji oraz terminy wykonania pozostają odrębnymi krokami.",
        "",
    ]
    for card in report["cards"]:
        lines.extend(
            [
                f"## {card['card_id']} — {cell(card['organization'])}: {cell(card['name'])}",
                f"- Właściciel: {card['owner_subepic']}; "
                f"stan rejestru: {card['integration_state']}.",
                f"- Zakres wymagany z workbook: {cell(card['expected_scope_raw'])}.",
                f"- Tematy: {cell(card['expected_topics_raw'])}; obszar: "
                f"{cell(card['expected_region_raw'])}; interwał wymagany: "
                f"{cell(card['expected_poll_minutes_raw'])} min.",
                "- Skonfigurowane kanały informacji: "
                + (
                    "; ".join(
                        f"{cell(s['name'])} [{s['state']}]: {cell(s['scope'])}"
                        for s in card["information_states"]
                    )
                    or "brak"
                )
                + ".",
                "- Skonfigurowane kanały danych: "
                + (
                    "; ".join(
                        f"{cell(c['name'])} [{c['kind']}/{c['state']}]" for c in card["data_states"]
                    )
                    or "brak"
                )
                + ".",
                "- Wymagany przegląd techniczny: "
                + (
                    "; ".join(cell(reason) for reason in card["technical_reasons"])
                    or "brak flag; NIE dowód odbioru"
                )
                + ".",
                "- Zadania i następny krok: "
                + "; ".join(
                    f"{task['id']} [{task['stage']}/{task['state']}/{task['owner']}]: "
                    f"{cell(task['action'])}"
                    for task in card["work_items"]
                )
                + ".",
                "- Produkty/warstwy/okresy i dowody kompletności: UNCONFIRMED; "
                "właściciel decyzji: operator z propozycją asystenta; "
                f"odbiór: {card['acceptance_status']}.",
                "",
            ]
        )
    value = "\n".join(lines) + "\n"
    if len(value.encode()) > RESULT_BYTES:
        raise FetchError("Scope-card output budget exceeded")
    return value
