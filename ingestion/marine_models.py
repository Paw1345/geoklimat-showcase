"""Bounded, versioned metadata report for a Marine catalogue scan."""

import json
import re

from django.core.exceptions import ValidationError
from django.db import models

from ingestion.marine import URL, product_url
from ingestion.transport import FetchError

MAX_REPORT_BYTES = 1024 * 1024
MAX_PRODUCTS = 500
PROFILE = "marine_missing_metadata_v1"
FIELDS = frozenset(
    {
        "version",
        "profile",
        "root_url",
        "root_sha256",
        "manifest",
        "inspected_count",
        "valid_count",
        "scan_complete",
        "metadata_complete",
        "issues",
    }
)


def invalid():
    raise ValidationError("Invalid or unsupported Marine catalogue report")


def validate_report(payload):
    """Fail closed on untrusted, unknown or too large reports, including reads."""
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        invalid()
    if len(encoded) > MAX_REPORT_BYTES or not isinstance(payload, dict) or set(payload) != FIELDS:
        invalid()
    if type(payload["version"]) is not int or payload["version"] != 1:
        invalid()
    if payload["profile"] != PROFILE or payload["root_url"] != URL:
        invalid()
    if not isinstance(payload["root_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", payload["root_sha256"]
    ):
        invalid()
    manifest = payload["manifest"]
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= MAX_PRODUCTS:
        invalid()
    if any(
        not isinstance(value, str)
        or len(value) > 1400
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
        for value in manifest
    ):
        invalid()
    if len(set(manifest)) != len(manifest):
        invalid()
    inspected, valid = payload["inspected_count"], payload["valid_count"]
    if (
        type(inspected) is not int
        or type(valid) is not int
        or not 0 <= valid <= inspected <= len(manifest)
    ):
        invalid()
    scan = payload["scan_complete"]
    metadata = payload["metadata_complete"]
    if type(scan) is not bool or (metadata is not None and type(metadata) is not bool):
        invalid()
    issues = payload["issues"]
    if not isinstance(issues, list) or len(issues) > MAX_PRODUCTS:
        invalid()
    seen = set()
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {"product_id", "url", "missing_fields"}:
            invalid()
        key, url, fields = issue["product_id"], issue["url"], issue["missing_fields"]
        if not isinstance(key, str) or key not in manifest[:inspected] or key in seen:
            invalid()
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(field, str) for field in fields)
        ):
            invalid()
        if len(set(fields)) != len(fields) or not set(fields) <= {"title", "license"}:
            invalid()
        try:
            if (
                not isinstance(url, str)
                or len(url) > 1500
                or product_url(url) != url
                or url != f"https://stac.marine.copernicus.eu/metadata/{key}/product.stac.json"
            ):
                invalid()
        except (FetchError, ValueError):
            invalid()
        seen.add(key)
    if valid + len(issues) != inspected:
        invalid()
    if scan:
        if (
            inspected != len(manifest)
            or valid + len(issues) != inspected
            or metadata is not (not issues)
        ):
            invalid()
    elif metadata is not None or inspected == len(manifest):
        invalid()


class MarineCatalogueReport(models.Model):
    run = models.OneToOneField(
        "ingestion.IngestionRun", on_delete=models.PROTECT, related_name="marine_report"
    )
    payload = models.JSONField()

    def clean(self):
        super().clean()
        validate_report(self.payload)
        if self.run_id is not None:
            source = self.run.source
            if source.slug != "registry-195" or source.adapter != "stac" or source.url != URL:
                raise ValidationError("Marine report requires the reviewed Marine source")

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)
