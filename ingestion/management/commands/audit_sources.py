import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.catalog import CATALOG
from ingestion.marine_audit import (
    marine_partial_audit_valid,
    marine_policy_hash,
    marine_profile_valid,
    marine_target,
)
from ingestion.marine_history import append_audit
from ingestion.models import Source
from ingestion.service import batch_lock, fetch_entries
from ingestion.transport import Client, FetchError


def audit_marine(source, client, hold, enable_verified, *, partial_audit=False):
    """Keep HTTP outside locks and never overwrite a newer administrator decision."""
    expected = marine_policy_hash(source)
    profile_check = marine_partial_audit_valid if partial_audit else marine_profile_valid
    approved_profile = profile_check(source)
    result = {"source": source.slug, "url": source.url, "policy_hash": expected}
    if partial_audit and not approved_profile:
        return {
            "source": source.slug,
            "status": "manual_review",
            "error": "Partial Marine audit requires a disabled, unapproved reviewed profile",
        }
    if not approved_profile:
        result.update(status="manual_review", error="Marine profile or linkage requires review")
    else:
        try:
            if partial_audit:
                from ingestion.marine import collect_catalogue

                catalogue = collect_catalogue(
                    source, client, allow_partial=True, audit_partial=True
                )
                from ingestion.service import _marine_report_payload

                report = _marine_report_payload(catalogue)
                entries, rejected, response = (
                    catalogue.entries,
                    catalogue.rejected,
                    catalogue.response,
                )
                result.update(
                    profile="marine_missing_metadata_v1",
                    root_sha256=catalogue.root_sha256,
                    scan_complete=True,
                    metadata_complete=rejected == 0,
                    marine_report=report,
                )
            else:
                entries, rejected, response, _ = fetch_entries(source, client, conditional=False)
            result.update(
                status="manual_review"
                if partial_audit
                else (
                    "partial"
                    if rejected
                    else ("manual_review" if source.slug in hold else "verified")
                ),
                http=response.status,
                count=len(entries),
                rejected=rejected,
                final_url=response.url,
                robots=response.access_policy,
            )
        except Exception as exc:
            result.update(
                status="unverified",
                error=str(exc) if isinstance(exc, FetchError) else type(exc).__name__,
            )
    with transaction.atomic():
        current = Source.objects.select_for_update().filter(pk=source.pk).first()
        if (
            current is None
            or marine_policy_hash(current) != expected
            or marine_policy_hash(source) != expected
            or current.enabled != source.enabled
            or current.activation_approved != source.activation_approved
            or current.last_audit != source.last_audit
            or current.audited_at != source.audited_at
            or profile_check(current, lock=True) != approved_profile
        ):
            return {"source": source.slug, "status": "stale"}
        current.last_audit = result
        current.audited_at = timezone.now()
        fields = ["last_audit", "audited_at"]
        if (
            enable_verified
            and result["status"] == "verified"
            and result["count"] > 0
            and result["rejected"] == 0
            and current.activation_approved
        ):
            current.enabled = True
            fields.append("enabled")
        current.save(update_fields=fields)
        append_audit(current, result)
    return result


class Command(BaseCommand):
    help = "Validate robots, HTTP and parser; persist evidence; optionally enable verified sources."

    def add_arguments(self, parser):
        parser.add_argument("--source")
        parser.add_argument("--enable-verified", action="store_true")
        parser.add_argument("--audit-marine-partial", action="store_true")
        parser.add_argument("--registry-priority", choices=["P0", "P1", "P2", "P3"])
        parser.add_argument("--new-only", action="store_true")
        parser.add_argument("--unaudited-only", action="store_true")
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
        if options["audit_marine_partial"] and options["source"] != "registry-195":
            raise CommandError("Partial Marine audit requires --source registry-195")
        sources = Source.objects.all()
        if options["source"]:
            sources = sources.filter(slug=options["source"])
        if options["registry_priority"]:
            sources = sources.filter(
                registry_entries__priority=options["registry_priority"]
            ).distinct()
        if options["new_only"]:
            sources = sources.filter(slug__startswith="registry-")
        if options["unaudited_only"]:
            sources = sources.filter(audited_at__isnull=True)
        if options["limit"]:
            sources = sources[: max(1, options["limit"])]
        hold = {row["slug"] for row in CATALOG if row.get("manual_hold")}
        client = Client()
        with batch_lock() as acquired:
            if not acquired:
                self.stdout.write("Ingestion active; retry audit later")
                return
            for source in sources:
                if marine_target(source):
                    result = audit_marine(
                        source,
                        client,
                        hold,
                        options["enable_verified"],
                        partial_audit=options["audit_marine_partial"],
                    )
                    summary = {
                        key: value for key, value in result.items() if key != "marine_report"
                    }
                    self.stdout.write(json.dumps(summary, ensure_ascii=False))
                    continue
                result = {"source": source.slug, "url": source.url}
                try:
                    entries, rejected, response, _ = fetch_entries(
                        source, client, conditional=False
                    )
                    result.update(
                        status="verified" if source.slug not in hold else "manual_review",
                        http=response.status,
                        count=len(entries),
                        rejected=rejected,
                        final_url=response.url,
                        robots=response.access_policy,
                    )
                except Exception as exc:
                    result.update(
                        status="unverified",
                        error=str(exc) if isinstance(exc, FetchError) else type(exc).__name__,
                    )
                source.last_audit = result
                source.audited_at = timezone.now()
                fields = ["last_audit", "audited_at"]
                if options["enable_verified"]:
                    source.enabled = (
                        result["status"] == "verified"
                        and result["count"] > 0
                        and source.activation_approved
                        and not result["rejected"]
                        and not (
                            source.config.get("mode") == "ems_initial_activations"
                            and source.config.get("terms_accepted") is not True
                        )
                    )
                    fields.append("enabled")
                source.save(update_fields=fields)
                self.stdout.write(json.dumps(result, ensure_ascii=False))
