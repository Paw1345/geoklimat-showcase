import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from ingestion.catalog import CATALOG
from ingestion.models import Source
from ingestion.service import batch_lock, fetch_entries
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Validate robots, HTTP and parser; persist evidence; optionally enable verified sources."

    def add_arguments(self, parser):
        parser.add_argument("--source")
        parser.add_argument("--enable-verified", action="store_true")
        parser.add_argument("--registry-priority", choices=["P0", "P1", "P2", "P3"])
        parser.add_argument("--new-only", action="store_true")
        parser.add_argument("--unaudited-only", action="store_true")
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
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
