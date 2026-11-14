"""Read only the reviewed historical FIGARO2015 table; preserve source scalars."""

import csv
import hashlib
import io
import re
from collections import Counter
from decimal import Decimal

from django.db import connection
from django.db.models import BigIntegerField, F, Func

from ingestion.file_exports import write_verified_file
from ingestion.transport import FetchError

URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/FIGARO-E3/"
    "Energy%20and%20emissions/flatfile_FIGARO-e_AEM_Gg_2015.csv"
)
SLUG = "jrc-file-203f03987dfb7e2a"
CONFIG = {
    "mode": "jrc_file",
    "dataset": "http://data.europa.eu/89h/0466566d-3e48-4537-a6cf-ad70921e56e5",
    "license": "http://publications.europa.eu/resource/authority/licence/CC_BY_4_0",
    "distribution": "http://data.jrc.ec.europa.eu/distribution/9d493163-d4bc-5b22-922c-0582b8b0c900",
}
SIZE = 1439185
SHA256 = "910eb04281f661d901a62c0b001bcd7d2bb6fea23e487da9bb7c7b6e113d87b3"
MAX_BYTES = SIZE
MAX_ROWS = 33304
MAX_CELL = 64
COLUMNS = ("category", "codeIndicator", "refArea", "colPi", "timePeriod", "unit", "obsValue")
INDICATOR_UNITS = {"CO2": "kt", "CH4": "kt", "N2O": "kt", "F-gases": "kt CO2e"}
AREAS = frozenset(
    "AR AT AU BE BG BR CA CH CN CY CZ DE DK EE ES FI FIGW1 FR GB GR HR HU ID IE IN IT "
    "JP KR LT LU LV MT MX NL NO PL PT RO RU SA SE SI SK TR US ZA".split()
)
NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def parse_figaro(body):
    """Validate every row before selection; codes, signs and units are not normalized."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES or b"\x00" in body:
        raise FetchError("FIGARO input byte budget or encoding invalid")
    try:
        text = body.decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        if tuple(next(reader, ())) != COLUMNS:
            raise FetchError("FIGARO header differs from reviewed schema")
        rows, identities = [], set()
        indicators, areas = Counter(), Counter()
        negative = zero = negative_zero = trailing_spaces = 0
        for cells in reader:
            if (
                len(rows) >= MAX_ROWS
                or len(cells) != len(COLUMNS)
                or any(not cell or len(cell) > MAX_CELL for cell in cells)
            ):
                raise FetchError("FIGARO row shape or budget invalid")
            category, indicator, area, code, period, unit, value = cells
            if (
                category != "Air emissions"
                or area not in AREAS
                or period != "2015"
                or indicator not in INDICATOR_UNITS
                or unit != INDICATOR_UNITS[indicator]
                or code.isspace()
                or any(not 32 <= ord(char) <= 126 for char in code)
                or NUMBER.fullmatch(value) is None
            ):
                raise FetchError("FIGARO scalar outside reviewed schema")
            identity = tuple(cells[:-1])
            if identity in identities:
                raise FetchError("FIGARO duplicate source identity")
            identities.add(identity)
            number = Decimal(value)
            # Counting is not aggregation: never add values across colPi or units.
            negative += number < 0
            zero += number == 0
            negative_zero += value == "-0"
            trailing_spaces += code.endswith(" ")
            indicators[indicator] += 1
            areas[area] += 1
            rows.append(dict(zip(COLUMNS, cells, strict=True)))
        if not rows:
            raise FetchError("FIGARO table has no source records")
    except (UnicodeError, csv.Error) as error:
        raise FetchError("FIGARO UTF-8 or CSV invalid") from error
    return rows, {
        "schema": "figaro-air-emissions-2015-v1",
        "records": len(rows),
        "observation_year": "2015",
        "source_bytes": len(body),
        "source_sha256": hashlib.sha256(body).hexdigest(),
        "indicator_counts": dict(sorted(indicators.items())),
        "area_counts": dict(sorted(areas.items())),
        "indicator_units": INDICATOR_UNITS.copy(),
        "negative_values": negative,
        "zero_values": zero,
        "negative_zero_raw": negative_zero,
        "trailing_space_code_rows": trailing_spaces,
        "colPi_names_resolved": False,
        "values_aggregated": False,
        "geographic_scope": "source_codes_not_current_EU27",
    }


def read_verified_figaro(job, now):
    """Bound DB materialization before reusing the canonical whole-file integrity fence."""
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise FetchError("FIGARO reader requires a PostgreSQL read-only snapshot")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')"
        )
        if cursor.fetchone() != ("repeatable read", "on"):
            raise FetchError("FIGARO reader requires REPEATABLE READ READ ONLY")
    channel = job.channel
    if (
        channel.slug != SLUG
        or channel.kind != "file"
        or channel.region != "EU"
        or channel.url != URL
        or channel.config != CONFIG
        or not channel.access_approved
        or type(job.total_bytes) is not int
        or job.total_bytes != SIZE
        or job.sha256 != SHA256
        or not isinstance(channel.attribution, str)
        or not 0 < len(channel.attribution) <= 2000
    ):
        raise FetchError("FIGARO copy is not the reviewed original")
    # This reviewed 1.44MB download has one chunk. Read lengths, never payload, here.
    chunks = list(
        job.chunks.annotate(
            actual_bytes=Func(F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField())
        ).values_list("size_bytes", "actual_bytes")[:2]
    )
    if chunks != [(SIZE, SIZE)] or not 0 < SIZE <= MAX_BYTES:
        raise FetchError("FIGARO actual stored payload outside reviewed byte budget")
    stream = io.BytesIO()
    write_verified_file(job, now, stream)
    rows, summary = parse_figaro(stream.getvalue())
    summary.update(
        job_id=job.pk,
        channel_id=channel.pk,
        source_url=URL,
        dataset=CONFIG["dataset"],
        distribution=CONFIG["distribution"],
        license=CONFIG["license"],
        attribution=channel.attribution,
        copied_at=job.created_at.isoformat(),
        expires_at=job.expires_at.isoformat(),
        retention_basis="local_copy_age",
    )
    return rows, summary
