import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.epic45_acceptance import apply_states, state_plan
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = (
        "Write registry integration_state for K6 decisions recorded in the acceptance ledger. "
        "Dry-run by default; --apply writes only integration_state/integration_note."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--registry-id", type=int, action="append", dest="registry_ids")

    def handle(self, *args, **options):
        try:
            if options["apply"]:
                plan = apply_states(options["registry_ids"])
            else:
                plan, _ = state_plan(options["registry_ids"])
        except FetchError as exc:
            raise CommandError(str(exc)) from exc
        result = {
            "apply": options["apply"],
            **plan,
            "written": len(plan["pending"]) if options["apply"] else 0,
            "http_requests": False,
        }
        self.stdout.write(json.dumps(result, ensure_ascii=False))
