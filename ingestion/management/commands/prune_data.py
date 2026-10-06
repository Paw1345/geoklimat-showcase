from django.core.management.base import BaseCommand

from ingestion.data_retention import prune_cache


class Command(BaseCommand):
    help = "Trim expired observations and data cache only; never removes news or registry."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        stats = prune_cache(apply=options["apply"])
        self.stdout.write(f"{stats}, applied={options['apply']}")
