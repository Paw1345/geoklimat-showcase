import os
from contextlib import ExitStack
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingestion.collection_receipt import FAILURE, ReceiptError, preview


def write_receipt(target, output):
    """Create one private file under existing trusted directories; never overwrite/delete."""
    if (
        type(target) is not str
        or not 0 < len(target) <= 4096
        or "\x00" in target
        or "\\" in target
        or any(part in (".", "..") for part in target.split("/"))
    ):
        raise ValueError("Invalid output")
    path = Path(target)
    root = Path(settings.BASE_DIR) / "backups" / "collection-receipts"
    if not path.is_absolute() or path.parent != root or path.suffix != ".json":
        raise ValueError("Private output required")
    data = output.encode("utf-8")  # Serialization/encoding completes before file creation.
    # Container-first POSIX: fail closed rather than fall back to race-prone path opens.
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("Safe directory operations unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = os.open(root.anchor, flags)
        stack.callback(os.close, directory)
        for part in root.parts[1:]:
            directory = os.open(part, flags, dir_fd=directory)
            stack.callback(os.close, directory)
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        stack.callback(os.close, descriptor)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            if stream.write(data) != len(data):
                raise OSError("Incomplete receipt")
        # All streams/descriptors close before the command can report success.


class Command(BaseCommand):
    help = (
        "Preview an unsigned historical IMGW collection receipt for exactly three "
        "reviewed PL API channels. JSON stdout; optional exclusive private --output, "
        "no collection. A successful export is not continuity PASS."
    )
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--channel", type=int, action="append")
        parser.add_argument(
            "--output", help="New absolute .json in existing backups/collection-receipts"
        )

    def handle(self, *args, **options):
        try:
            output = preview(options["channel"])
            if options["output"] is not None:
                write_receipt(options["output"], output)
        except (ReceiptError, OSError, ValueError, TypeError, NotImplementedError):
            raise CommandError(FAILURE) from None
        self.stdout.write(output, ending="")
