"""Read-only schema inspection of the explicitly selected public monthly archive.

Run inside the project container via manage.py shell. No extraction to disk.
"""

import csv
import hashlib
import io
import json
import zipfile
from collections import Counter

from ingestion.transport import Client

ROOT = (
    "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/"
    "dane_meteorologiczne/dobowe/synop/"
)
client = Client()
header = client.get(ROOT + "s_d_nag%C5%82%C3%B3wek.csv")
print("HEADER", header.body.decode("cp1250"), flush=True)
response = client.get(ROOT + "2026/2026_08_s.zip")
with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
    for info in archive.infolist():
        if info.file_size > 5 * 1024 * 1024:
            raise ValueError("Uncompressed inspection budget exceeded")
        body = archive.read(info)
        rows = list(csv.reader(io.StringIO(body.decode("cp1250"))))
        print(
            json.dumps(
                {
                    "member": info.filename,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "rows": len(rows),
                    "widths": dict(Counter(map(len, rows))),
                    "columns": [sorted({row[i] for row in rows})[:12] for i in range(len(rows[0]))],
                    "first": rows[0],
                    "last": rows[-1],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
