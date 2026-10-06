import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingestion.local_ai import material, request_model, validate_output
from ingestion.models import Item


class Command(BaseCommand):
    help = (
        "Read-only DB probe of one local AI summary; dry-run default, --apply runs local inference."
    )

    def add_arguments(self, parser):
        parser.add_argument("item", type=int)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        item = Item.objects.get(pk=options["item"])
        data = material(item)
        if not options["apply"]:
            self.stdout.write(
                f"Item {item.pk}; description characters={len(data['summary'])}; no inference."
            )
            return
        if not settings.LOCAL_AI_ENABLED:
            raise CommandError("Local AI is disabled")
        result = validate_output(request_model(data), data)
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
