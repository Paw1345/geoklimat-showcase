import json
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from ingestion import stac_audit
from ingestion.stac_integrity import invalid_constant, unique_object
from ingestion.transport import FetchError


def read_document(filename, limit):
    with Path(filename).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise FetchError("Audit document too large")
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)


class Command(BaseCommand):
    help = "Read-only STAC audit: signed plan, bounded batch proof, merge without payload reads."

    def add_arguments(self, parser):
        parser.add_argument("mode", choices=["plan", "batch", "combine"])
        parser.add_argument("--channel", type=int)
        parser.add_argument("--harvest", type=int)
        parser.add_argument("--plan")
        parser.add_argument("--start", type=int)
        parser.add_argument("--proof", action="append", default=[])
        parser.add_argument(
            "--output", help="New JSON evidence file; refuses overwrite. Default stdout."
        )

    def handle(self, *args, **options):
        try:
            mode = options["mode"]
            if mode == "plan":
                if (
                    not options["channel"]
                    or not options["harvest"]
                    or options["plan"]
                    or options["proof"]
                    or options["start"]
                ):
                    raise FetchError("Plan needs channel and harvest only")
                result = stac_audit.plan(options["channel"], options["harvest"])
            else:
                if not options["plan"] or options["channel"] or options["harvest"]:
                    raise FetchError("Batch/merge requires plan only")
                plan = read_document(options["plan"], 5 * 1024 * 1024)["plan"]
                if mode == "batch":
                    if options["proof"] or not options["start"]:
                        raise FetchError("Batch needs aligned start")
                    result = stac_audit.batch(plan, options["start"])
                else:
                    if options["start"] or not 1 <= len(options["proof"]) <= 400:
                        raise FetchError("Merge requires proofs")
                    proofs, total = [], 0
                    for filename in options["proof"]:
                        item = read_document(filename, 1024 * 1024)
                        total += len(json.dumps(item).encode())
                        if total > 32 * 1024 * 1024:
                            raise FetchError("Aggregate audit input budget exceeded")
                        proofs.append(item["proof"])
                    result = stac_audit.combine(plan, proofs)
            output = json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False)
            if options["output"]:
                with Path(options["output"]).open("x", encoding="utf-8") as stream:
                    stream.write(output)
                self.stdout.write("New audit evidence file created; no database writes or HTTP.")
            else:
                self.stdout.write(output)
        except (
            FetchError,
            ObjectDoesNotExist,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RecursionError,
        ) as exc:
            raise CommandError(
                "STAC audit unavailable: scope, evidence, expiry, integrity, budget or file."
            ) from exc
