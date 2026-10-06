"""Only explicit identifiers and source dates; no legal inference from headlines."""

import hashlib
import json
import re
from datetime import date
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from ingestion.models import LegalEvent, LegalLink, LegalProcess, LegalRelation
from ingestion.transport import FetchError

ELI = re.compile(r"(?:DU|MP)/[12][0-9]{3}/[1-9][0-9]*")
NUMBER = re.compile(r"[0-9]+(?:-[A-Za-z0-9]+)*")
ELI_FIELDS = (
    "ELI",
    "status",
    "type",
    "announcementDate",
    "promulgation",
    "entryIntoForce",
    "inForce",
    "references",
)
SEJM_FIELDS = (
    "number",
    "term",
    "ELI",
    "documentDate",
    "processStartDate",
    "closureDate",
    "passed",
    "stages",
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def source_identity(item):
    parts = urlsplit(item.url)
    if parts.scheme != "https" or parts.query or parts.fragment or parts.username:
        return None
    if item.source.adapter == "eli" and parts.netloc == "eli.gov.pl":
        key = item.metadata.get("ELI", "")
        if isinstance(key, str) and ELI.fullmatch(key) and parts.path == f"/eli/{key}/ogl":
            return "ELI:" + key, "https://api.sejm.gov.pl/eli/acts/" + key, "eli"
    match = re.fullmatch(
        r"/sejm/term([1-9][0-9]*)/processes/([0-9]+(?:-[A-Za-z0-9]+)*)", parts.path
    )
    if (
        item.source.adapter == "json"
        and item.source.config.get("mode") == "sejm"
        and parts.netloc == "api.sejm.gov.pl"
        and match
    ):
        return f"SEJM:{match[1]}/{match[2]}", item.url, "sejm"
    return None


def calendar_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise FetchError(f"Invalid legislative date: {field}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FetchError(f"Invalid legislative date: {field}") from exc


def observations(data, source_kind):
    fields = (
        {"announcementDate": "issued", "promulgation": "published", "entryIntoForce": "effective"}
        if source_kind == "eli"
        else {"processStartDate": "process_start", "closureDate": "process_end"}
    )
    events = []
    for field, kind in fields.items():
        value = data.get(field)
        if value not in (None, ""):
            events.append(
                (
                    field,
                    kind,
                    calendar_date(value, field),
                    dict(LegalEvent.KINDS)[kind],
                    f"{field} = {value}",
                )
            )
    # Source stages are preserved only when both the date and label are explicit.
    stages = data.get("stages", []) if source_kind == "sejm" else []
    if not isinstance(stages, list) or len(stages) > 500:
        raise FetchError("Legislative stages schema or budget changed")
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise FetchError("Invalid legislative stage")
        if stage.get("date") and stage.get("stageName"):
            events.append(
                (
                    f"stage:{index}",
                    "stage",
                    calendar_date(stage["date"], "stage.date"),
                    str(stage["stageName"])[:1000],
                    f"stages[{index}].date = {stage['date']}; stageName = {stage['stageName']}",
                )
            )
    relations = []
    if source_kind == "sejm" and data.get("ELI"):
        # Some official rows contain truncated/multiple identifiers, not one ELI.
        # Preserve the raw evidence and flag it in the UI; never split or guess a target.
        if isinstance(data["ELI"], str) and ELI.fullmatch(data["ELI"]):
            relations.append(("ELI:" + data["ELI"], "published_as", "ELI = " + data["ELI"]))
    if source_kind == "eli":
        refs = data.get("references") or {}
        if not isinstance(refs, dict) or len(refs) > 100:
            raise FetchError("ELI references schema changed")
        for kind, rows in refs.items():
            if not isinstance(rows, list) or len(rows) > 1000:
                raise FetchError("ELI relation budget exceeded")
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not ELI.fullmatch(row["id"])
                ):
                    raise FetchError("Invalid related ELI identifier")
                relations.append(
                    ("ELI:" + row["id"], str(kind)[:40], f"references.{kind}: {row['id']}")
                )
                if len(relations) > 1000:
                    raise FetchError("ELI total relation budget exceeded")
    return events, list(dict.fromkeys(relations))


def listing_data(item, kind):
    data = item.metadata.get("legislation", {}).copy()
    if kind == "eli":
        data.update({key: item.metadata.get(key) for key in ELI_FIELDS if key in item.metadata})
        data.setdefault("promulgation", item.metadata.get("publication_date_raw", ""))
    return data


def fetch_details(item, client):
    identity = source_identity(item)
    if not identity:
        raise FetchError("No reviewed legislative detail endpoint for this item")
    identifier, url, kind = identity
    response = client.get(url)
    data = json.loads(response.body)
    if response.url != url or not isinstance(data, dict):
        raise FetchError("Legislative detail endpoint or schema changed")
    if kind == "eli" and data.get("ELI") != identifier[4:]:
        raise FetchError("ELI identity mismatch")
    if kind == "sejm" and f"SEJM:{data.get('term')}/{data.get('number')}" != identifier:
        raise FetchError("Sejm identity mismatch")
    fields = ELI_FIELDS if kind == "eli" else SEJM_FIELDS
    selected = {key: data[key] for key in fields if key in data}
    observations(selected, kind)
    return selected


@transaction.atomic
def sync_item(item, data=None):
    from ingestion.changes import ensure_thread

    identity = source_identity(item)
    if not identity:
        return None
    identifier, url, kind = identity
    origin = "api" if data is not None else "listing"
    data = listing_data(item, kind) if data is None else data
    events, relations = observations(data, kind)
    process, _ = LegalProcess.objects.get_or_create(
        identifier=identifier, defaults={"title": item.title, "jurisdiction": "PL"}
    )
    process.title = item.title
    process.save(update_fields=["title"])
    ensure_thread(process)
    digest = fingerprint(data)
    link, created = LegalLink.objects.get_or_create(
        process=process,
        item=item,
        origin=origin,
        defaults={
            "evidence_url": url if origin == "api" else item.url,
            "evidence": json.dumps(data, ensure_ascii=False),
            "item_hash": item.content_hash,
            "evidence_hash": digest,
        },
    )
    if not created and link.item_hash == item.content_hash and link.evidence_hash == digest:
        return process
    now = timezone.now()
    link.events.filter(superseded_at__isnull=True).update(superseded_at=now)
    link.relations.filter(superseded_at__isnull=True).update(superseded_at=now)
    link.item_hash, link.evidence_hash, link.observed_at = item.content_hash, digest, now
    link.evidence = json.dumps(data, ensure_ascii=False)
    link.save()
    for key, event_kind, day, description, evidence in events:
        LegalEvent.objects.create(
            link=link,
            event_key=key,
            kind=event_kind,
            date=day,
            description=description,
            evidence=evidence,
            evidence_url=link.evidence_url,
            item_hash=item.content_hash,
        )
    for target_id, relation_kind, evidence in relations:
        if target_id == identifier:
            continue
        target, _ = LegalProcess.objects.get_or_create(
            identifier=target_id, defaults={"title": target_id, "jurisdiction": "PL"}
        )
        LegalRelation.objects.create(
            link=link,
            target=target,
            kind=relation_kind,
            evidence=evidence,
            item_hash=item.content_hash,
        )
    return process


def current_events(queryset):
    # Detailed API facts replace the same list-derived field, while old facts remain in history.
    from django.db.models import Exists, F, OuterRef, Q

    details = LegalEvent.objects.filter(
        link__origin="api",
        link__item_id=OuterRef("link__item_id"),
        event_key=OuterRef("event_key"),
        superseded_at__isnull=True,
        item_hash=F("link__item__content_hash"),
    )
    return (
        queryset.filter(superseded_at__isnull=True, item_hash=F("link__item__content_hash"))
        .annotate(has_detail=Exists(details))
        .exclude(Q(link__origin="listing", has_detail=True))
    )
