"""Published daily SYNOP archives, filtered before storage; no ZIP extraction.

Schema reviewed against IMGW s_d_format.txt and s_d_nagłówek.csv on 2026-09-24.
CSV cells remain strings: a blank, status 8/9 and the number zero are distinct.
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.conf import settings

from ingestion.observations import daily_window, filter_observations, observation_summary
from ingestion.transport import FetchError

ROOT = (
    "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/"
    "dane_meteorologiczne/dobowe/synop/"
)
COLUMNS = (
    "NSP POST ROK MC DZ TMAX WTMAX TMIN WTMIN STD WSTD TMNG WTMNG SMDB WSMDB "
    "ROOP PKSN WPKSN RWSN WRWSN USL WUSL DESZ WDESZ SNEG WSNEG DISN WDISN "
    "GRAD WGRAD MGLA WMGLA ZMGL WZMGL SADZ WSADZ GOLO WGOLO ZMNI WZMNI "
    "ZMWS WZMWS ZMET WZMET FF10 WFF10 FF15 WFF15 BRZA WBRZA ROSA WROSA "
    "SZRO WSZRO DZPS WDZPS DZBL WDZBL SGR IZD WIZD IZG WIZG AKTN WAKTN"
).split()
UNITS = {
    **dict.fromkeys(["TMAX", "TMIN", "STD", "TMNG"], "°C"),
    "SMDB": "mm",
    "PKSN": "cm",
    "RWSN": "mm/cm",
    **dict.fromkeys(
        (
            "USL DESZ SNEG DISN GRAD MGLA ZMGL SADZ GOLO ZMNI ZMWS ZMET FF10 FF15 BRZA ROSA SZRO"
        ).split(),
        "h",
    ),
    "DZPS": "0/1",
    "DZBL": "0/1",
    "IZD": "cm",
    "IZG": "cm",
    "AKTN": "J/cm2",
}
CONFIG = {"mode": "daily_synop_v1"}
MAX_UNPACKED_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20000
ATTRIBUTION = (
    "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej "
    "– Państwowy Instytut Badawczy. Dane Instytutu Meteorologii i Gospodarki Wodnej "
    "– Państwowego Instytutu Badawczego zostały przetworzone. "
    "Dobowe CSV SYNOP: tylko opublikowane miesiące i rekordy mieszczące się w 30 dniach. "
    "Data dzienna nie określa godziny pomiaru; retencja konserwatywnie od początku "
    "dnia w Europe/Warsaw. Kody i precyzja CSV zachowane bez konwersji braków na zero. "
    "Warunki ponownego wykorzystania: https://danepubliczne.imgw.pl/pl/regulations"
)


def fetch(client, url):
    response = client.get(url)
    if response.status != 200 or response.url != url:
        raise FetchError("IMGW archive response left pinned endpoint")
    return response.body


def listing(client, url):
    soup = BeautifulSoup(fetch(client, url), "html.parser")
    heading = soup.find("h1")
    if not heading or heading.get_text(strip=True) != "Index of " + urlsplit(url).path.rstrip("/"):
        raise FetchError("IMGW archive directory schema changed")
    links = [a.get("href", "") for a in soup.select("a[href]")]
    if len(links) > 200:
        raise FetchError("IMGW archive directory budget exceeded")
    if len(links) != len(set(links)):
        raise FetchError("IMGW archive duplicate directory link")
    return links


def parse_archive(body, year, month):
    """Validate every row, including rows excluded by retention, before returning."""
    expected = f"s_d_{month:02d}_{year}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            if (
                len(members) != 1
                or members[0].filename != expected
                or members[0].flag_bits & 1
                or members[0].file_size > MAX_UNPACKED_BYTES
                or members[0].file_size > max(1, members[0].compress_size) * 200
                or members[0].compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise FetchError("IMGW ZIP member/schema/expansion budget invalid")
            with archive.open(members[0]) as stream:
                raw = stream.read(MAX_UNPACKED_BYTES + 1)
            if len(raw) > MAX_UNPACKED_BYTES:
                raise FetchError("IMGW uncompressed CSV exceeds budget")
        reader = csv.reader(io.StringIO(raw.decode("cp1250")), strict=True)
        rows, seen = [], set()
        for cells in reader:
            if len(cells) != len(COLUMNS) or any(len(cell) > 150 for cell in cells):
                raise FetchError("IMGW daily CSV column schema changed")
            row = dict(zip(COLUMNS, cells, strict=True))
            if not re.fullmatch(r"[0-9]{9}", row["NSP"]) or not row["POST"].strip():
                raise FetchError("IMGW daily station identity missing")
            if (row["ROK"], row["MC"]) != (str(year), f"{month:02d}"):
                raise FetchError("IMGW daily row left requested month")
            if not re.fullmatch(r"[0-9]{2}", row["DZ"]):
                raise FetchError("IMGW daily day schema changed")
            day = date(year, month, int(row["DZ"]))
            for code in COLUMNS[5:]:
                value = row[code]
                if code.startswith("W"):
                    valid = value in {"", "8", "9"}
                elif code == "ROOP":
                    valid = value in {"", " ", "S", "W"}
                elif code == "SGR":
                    valid = value in {"", " ", "Z", "R"}
                elif code in {"DZPS", "DZBL"}:
                    valid = value in {"", "0", "1"}
                else:
                    valid = not value or bool(re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value))
                if not valid:
                    raise FetchError(f"IMGW daily value/status schema changed: {code}")
            key = (row["NSP"], day)
            if key in seen or len(rows) >= MAX_ROWS:
                raise FetchError("IMGW daily duplicate station/day or row budget exceeded")
            seen.add(key)
            rows.append({"date": day.isoformat(), **row})
        if not rows:
            raise FetchError("IMGW daily published archive is empty")
    except (zipfile.BadZipFile, UnicodeError, csv.Error, ValueError, OSError, RuntimeError) as exc:
        raise FetchError("Invalid IMGW daily ZIP/CSV") from exc
    return rows, hashlib.sha256(raw).hexdigest()


def collect_daily(channel, client, now):
    if (channel.url, channel.region, channel.config) != (ROOT, "PL", CONFIG):
        raise FetchError("IMGW daily profile differs from reviewed scope")
    header = list(
        csv.reader(io.StringIO(fetch(client, ROOT + "s_d_nag%C5%82%C3%B3wek.csv").decode("cp1250")))
    )
    if header != [COLUMNS]:
        raise FetchError("IMGW daily official header changed")
    months = sorted({(day.year, day.month) for day in daily_window(now)})
    if len(months) > 3:
        raise FetchError("IMGW backfill month budget exceeded")
    years = listing(client, ROOT)
    indexes = {}
    rows, archives, unavailable = [], [], []
    for year, month in months:
        year_url = ROOT + f"{year}/"
        filename = f"{year}_{month:02d}_s.zip"
        if f"{year}/" not in years:
            unavailable.append(f"{year}-{month:02d}")
            continue
        if year not in indexes:
            indexes[year] = listing(client, year_url)
        if filename not in indexes[year]:
            unavailable.append(f"{year}-{month:02d}")
            continue
        url = year_url + filename
        body = fetch(client, url)
        parsed, csv_hash = parse_archive(body, year, month)
        rows.extend(parsed)
        archives.append(
            {"url": url, "zip_sha256": hashlib.sha256(body).hexdigest(), "csv_sha256": csv_hash}
        )
    data = {
        "schema": "imgw-daily-synop-v1",
        "columns": COLUMNS,
        "units": UNITS,
        "status_codes": {"8": "brak pomiaru", "9": "brak zjawiska"},
        "format_url": ROOT + "s_d_format.txt",
        "attribution": ATTRIBUTION,
        "archives": archives,
        "unpublished_months": unavailable,
        "records": sorted(rows, key=lambda row: (row["date"], row["NSP"])),
    }
    data, stamps = filter_observations(data, "imgw_daily", now)
    summary = observation_summary(
        {"kind": "imgw_daily", "scope": "PL", "records": len(data["records"]), "partial": True},
        stamps,
        "Europe/Warsaw",
    )
    summary.update(date_precision="day", coverage=data["coverage"], unpublished_months=unavailable)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("IMGW daily payload exceeds automatic budget")
    return payload, "application/json", summary
