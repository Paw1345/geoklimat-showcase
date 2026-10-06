import hashlib
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ingestion.models import DataChannel, DataSnapshot


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).digest()


class Command(BaseCommand):
    help = "Read-only, bounded STAC cache duplication report; does not estimate reclaimable space."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--limit", type=int, default=350)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 10000:
            raise CommandError("limit must be between 1 and 10000")
        channel = DataChannel.objects.filter(slug=options["source"], kind="stac").first()
        if channel is None:
            raise CommandError("Select an existing STAC channel")
        now = timezone.now()
        snapshots = DataSnapshot.objects.filter(
            channel=channel, download_job__isnull=True, expires_at__gt=now
        ).order_by("-fetched_at", "-pk")
        report = {
            "source": channel.slug,
            "as_of": now.isoformat(),
            "read_only": True,
            "selection": "newest unexpired snapshots; concurrent writes may change totals",
            "available_pages": snapshots.count(),
            "limit": options["limit"],
            "scanned_pages": 0,
            "invalid_pages": 0,
            "scanned_bytes": 0,
            "exact_duplicate_pages": 0,
            "pages_equal_except_links": 0,
            "bytes_in_pages_equal_except_links": 0,
            "unique_scene_ids": 0,
            "identical_scene_occurrences": 0,
            "additional_scene_variants": 0,
            "note": "Not a space-saving estimate: links and fetch provenance must be preserved.",
        }
        raw_hashes, page_hashes, scene_hashes = set(), set(), {}
        for snapshot in snapshots.only("payload", "size_bytes")[: options["limit"]].iterator(
            chunk_size=1
        ):
            report["scanned_pages"] += 1
            report["scanned_bytes"] += snapshot.size_bytes
            payload = bytes(snapshot.payload)
            raw_hash = hashlib.sha256(payload).digest()
            if raw_hash in raw_hashes:
                report["exact_duplicate_pages"] += 1
            raw_hashes.add(raw_hash)
            try:
                data = json.loads(payload)
                if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
                    raise ValueError
                features = data.get("features")
                if not isinstance(features, list):
                    raise ValueError
                entries = []
                for feature in features:
                    if not isinstance(feature, dict) or feature.get("type") != "Feature":
                        raise ValueError
                    key = (feature.get("collection"), feature.get("id"))
                    if not all(isinstance(value, str) and value for value in key):
                        raise ValueError
                    entries.append((key, fingerprint(feature)))
                page_hash = fingerprint(
                    {key: value for key, value in data.items() if key != "links"}
                )
            except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
                report["invalid_pages"] += 1
                continue
            if page_hash in page_hashes:
                report["pages_equal_except_links"] += 1
                report["bytes_in_pages_equal_except_links"] += snapshot.size_bytes
            page_hashes.add(page_hash)
            for key, feature_hash in entries:
                versions = scene_hashes.setdefault(key, set())
                if feature_hash in versions:
                    report["identical_scene_occurrences"] += 1
                elif versions:
                    report["additional_scene_variants"] += 1
                versions.add(feature_hash)
        report["unique_scene_ids"] = len(scene_hashes)
        report["truncated"] = report["available_pages"] > report["scanned_pages"]
        self.stdout.write(json.dumps(report, ensure_ascii=False))
