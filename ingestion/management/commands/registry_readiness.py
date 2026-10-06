import json

from django.core.management.base import BaseCommand, CommandError

from core.registry_readiness import Filters, filtered_report, report


class Command(BaseCommand):
    help = "Read-only local registry readiness; no HTTP, writes or business sign-off."

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=["text", "json"], default="text")
        parser.add_argument("--priority", default="")
        parser.add_argument("--attention", choices=["", "yes", "no"], default="")
        parser.add_argument("--query", default="")

    def handle(self, *args, **options):
        form = Filters(
            {
                "q": options["query"],
                "priority": options["priority"],
                "attention": options["attention"],
                "format": "json" if options["format"] == "json" else "",
            }
        )
        if not form.is_valid():
            raise CommandError("Invalid readiness filters; no report generated")
        result = filtered_report(report(), form.cleaned_data)
        if options["format"] == "json":
            self.stdout.write(json.dumps(result, ensure_ascii=False))
            return
        self.stdout.write(
            f"Registry: {result['registry_total']}; review: {result['attention_total']}; "
            f"filtered: {result['filtered_total']}; business sign-off: NOT INCLUDED"
        )
        for row in result["rows"]:
            self.stdout.write(
                f"{row['registry_id']} {row['priority']} {row['organization']} "
                f"{row['integration_state']} | "
                + ("; ".join(row["reasons"]) or "No technical flags")
            )
