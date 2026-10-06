"""Signed, expiring evidence across bounded STAC reads; no database writes."""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC

from django.core import signing
from django.db import connection, transaction
from django.db.models import BigIntegerField, F, Func
from django.utils import timezone

from ingestion.models import DataChannel
from ingestion.observations import retention_window
from ingestion.stac_coverage import aware, coverage
from ingestion.stac_integrity import PAGE_COUNT, verify_batch
from ingestion.stac_timeline import timeline
from ingestion.transport import FetchError

PLAN_BYTES = 4 * 1024 * 1024
PROOF_BYTES = 512 * 1024
MAX_AGE = 3600


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def seal(value, kind, budget):
    token = signing.dumps(value, salt="geoklimat.stac.audit." + kind, compress=True)
    if len(token.encode()) > budget:
        raise FetchError("STAC audit evidence budget exceeded")
    return token


def unseal(token, kind, budget):
    if not isinstance(token, str) or len(token.encode()) > budget:
        raise FetchError("STAC audit evidence budget exceeded")
    try:
        return signing.loads(token, salt="geoklimat.stac.audit." + kind, max_age=MAX_AGE)
    except (signing.BadSignature, ValueError, TypeError) as exc:
        raise FetchError("STAC audit evidence expired or invalid") from exc


@contextmanager
def read_scope(channel_id, harvest_id):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        channel = DataChannel.objects.get(pk=channel_id, kind="stac", access_approved=True)
        harvest = channel.harvests.defer("visited_urls", "page_fingerprints").get(pk=harvest_id)
        yield channel, harvest, timezone.now()


def inventory(channel, harvest, pages, now):
    status = coverage(channel, harvest, now)
    if status["issues"] or not 1 <= pages <= harvest.pages <= 10000 or status["search_end"] > now:
        raise FetchError("STAC audit scope unavailable")
    rows = list(
        channel.snapshots.filter(summary__harvest_id=harvest.pk, download_job__isnull=True)
        .annotate(
            actual_bytes=Func(F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField())
        )
        .order_by("pk")
        .values(
            "pk",
            "summary",
            "sha256",
            "size_bytes",
            "actual_bytes",
            "expires_at",
            "fetched_at",
            "media_type",
        )[:10001]
    )
    selected, seen, boundaries = [], set(), []
    if len(rows) > 10000:
        raise FetchError("STAC audit inventory budget exceeded")
    for row in rows:
        page = row["summary"].get("page")
        if type(page) is not int or not 1 <= page <= harvest.pages:
            raise FetchError("STAC audit page invalid")
        if page > pages:
            continue
        if page in seen or row["actual_bytes"] != row["size_bytes"]:
            raise FetchError("STAC audit duplicate page or size mismatch")
        seen.add(page)
        metadata = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}
        selected.append(
            {"page": page, "snapshot_id": row["pk"], "metadata_sha256": fingerprint(metadata)}
        )
        boundaries.extend((row["expires_at"], row["fetched_at"] + retention_window()))
        if row["summary"].get("records"):
            boundaries.append(aware(row["summary"].get("oldest_observation")) + retention_window())
    if seen != set(range(1, pages + 1)) or min(boundaries) <= now:
        raise FetchError("STAC audit missing or expired copies")
    return sorted(selected, key=lambda row: row["page"]), min(boundaries), status


def plan(channel_id, harvest_id):
    with read_scope(channel_id, harvest_id) as (channel, harvest, now):
        rows, expires, status = inventory(channel, harvest, harvest.pages, now)
        body = {
            "version": 1,
            "channel": channel.pk,
            "harvest": harvest.pk,
            "pages": harvest.pages,
            "config_hash": harvest.config_hash,
            "inventory": rows,
            "created_at": now.isoformat(),
            "retained_until": expires.isoformat(),
        }
        token = seal(body, "plan", PLAN_BYTES)
    return {
        "plan": token,
        "plan_sha256": fingerprint(body),
        "channel_id": channel_id,
        "harvest_id": harvest_id,
        "anchored_pages": body["pages"],
        "starts": list(range(1, body["pages"] + 1, PAGE_COUNT)),
        "scan_complete_at_plan": status["scan_complete"],
        "valid_seconds_max": MAX_AGE,
        "payloads_read": False,
        "read_only": True,
    }


def checked_plan(token, channel, harvest, now):
    body = unseal(token, "plan", PLAN_BYTES)
    if (
        body["version"] != 1
        or body["channel"] != channel.pk
        or body["harvest"] != harvest.pk
        or body["config_hash"] != harvest.config_hash
        or aware(body["retained_until"]) <= now
    ):
        raise FetchError("STAC audit plan scope expired or changed")
    rows, _, status = inventory(channel, harvest, body["pages"], now)
    if rows != body["inventory"]:
        raise FetchError("STAC audit inventory changed")
    return body, status


def batch(plan_token, start):
    initial = unseal(plan_token, "plan", PLAN_BYTES)
    with read_scope(initial["channel"], initial["harvest"]) as (channel, harvest, now):
        body, _ = checked_plan(plan_token, channel, harvest, now)
        if type(start) is not int or start not in range(1, body["pages"] + 1, PAGE_COUNT):
            raise FetchError("STAC audit batch not aligned to plan")
        scenes = []

        def collect(feature, stamp, snapshot_id, page, digest):
            scenes.append([feature["id"], digest, stamp.isoformat()])

        result = verify_batch(channel, harvest, now, start, on_scene=collect, stop=body["pages"])
        proof = seal(
            {
                "version": 2,
                "plan_sha256": fingerprint(body),
                "checked_at": now.isoformat(),
                "start": start,
                "end": result["end_page"],
                "scenes": scenes,
                "checked_bytes": result["checked_bytes"],
            },
            "proof",
            PROOF_BYTES,
        )
    return {"proof": proof, "verification": result, "read_only": True}


def combine(plan_token, proof_tokens):
    if not isinstance(proof_tokens, list) or len(proof_tokens) > 400:
        raise FetchError("STAC audit proof count budget exceeded")
    if (
        any(not isinstance(token, str) for token in proof_tokens)
        or sum(len(token.encode()) for token in proof_tokens) > 32 * 1024 * 1024
    ):
        raise FetchError("STAC audit aggregate proof budget exceeded")
    initial = unseal(plan_token, "plan", PLAN_BYTES)
    with read_scope(initial["channel"], initial["harvest"]) as (channel, harvest, now):
        body, status = checked_plan(plan_token, channel, harvest, now)
        expected = set(range(1, body["pages"] + 1, PAGE_COUNT))
        seen, identities, clocks = set(), {}, []
        occurrences, checked_bytes = 0, 0
        days, dated_proofs, oldest, newest = {}, True, None, None
        for token in proof_tokens:
            proof = unseal(token, "proof", PROOF_BYTES)
            start = proof["start"]
            stamp = aware(proof["checked_at"])
            if (
                proof["version"] not in {1, 2}
                or proof["plan_sha256"] != fingerprint(body)
                or start not in expected
                or start in seen
                or proof["end"] != min(body["pages"], start + PAGE_COUNT - 1)
                or stamp is None
                or not aware(body["created_at"]) <= stamp <= now
            ):
                raise FetchError("STAC audit proof scope duplicate or invalid")
            seen.add(start)
            clocks.append(stamp)
            checked_bytes += proof["checked_bytes"]
            dated_proofs = dated_proofs and proof["version"] == 2
            for scene in proof["scenes"]:
                if not isinstance(scene, list) or len(scene) != (3 if proof["version"] == 2 else 2):
                    raise FetchError("STAC audit scene evidence schema invalid")
                identity, digest = scene[:2]
                identities.setdefault(identity, set()).add(digest)
                occurrences += 1
                if proof["version"] == 2:
                    observation = aware(scene[2])
                    if (
                        observation is None
                        or not status["search_start"] <= observation <= status["search_end"]
                        or not now - retention_window() < observation <= now
                    ):
                        raise FetchError("STAC audit observation evidence invalid")
                    day = observation.astimezone(UTC).date().isoformat()
                    bucket = days.setdefault(day, {"occurrences": 0, "ids": set()})
                    bucket["occurrences"] += 1
                    bucket["ids"].add(identity)
                    oldest = min(oldest, observation) if oldest else observation
                    newest = max(newest, observation) if newest else observation
        if seen != expected:
            raise FetchError("STAC audit missing batch proofs")
    return {
        "as_of": now,
        "channel_id": channel.pk,
        "harvest_id": harvest.pk,
        "plan_sha256": fingerprint(body),
        "anchored_pages": body["pages"],
        "verified_batches": len(seen),
        "checked_bytes_across_batches": checked_bytes,
        "record_occurrences_in_manifest": occurrences,
        "distinct_scene_ids_in_manifest": len(identities),
        "repeated_occurrences_in_manifest": occurrences - len(identities),
        "scene_ids_with_multiple_variants_in_manifest": sum(
            len(v) > 1 for v in identities.values()
        ),
        "first_payload_check": min(clocks),
        "last_payload_check": max(clocks),
        "all_manifest_pages_verified_at_proof_times": True,
        "current_metadata_matches_manifest": True,
        "payloads_rechecked_at_merge": False,
        "manifest_covers_all_read_pages_at_merge": body["pages"] == harvest.pages,
        "scan_complete_at_merge": status["scan_complete"],
        "search_start": status["search_start"],
        "search_end": status["search_end"],
        "timeline": timeline(status, now, days if dated_proofs else None, oldest, newest),
        "full_current_30_day_coverage_verified": False,
        "geographic_mask_verified": False,
        "assets_downloaded": False,
        "read_only": True,
    }
