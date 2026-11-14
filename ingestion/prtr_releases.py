"""Bounded, non-network interpretation of public PL annual pollutant releases only."""

import hashlib
import io
import re
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal

from ingestion import gios_prtr_file as profile
from ingestion.file_exports import write_verified_file
from ingestion.transport import FetchError

NS = "{urn:eu:com:env:prtr:data:standard:2}"
MAX_BYTES = 5 * 1024 * 1024
MAX_NODES = 200000
MAX_DEPTH = 16
MAX_FACILITIES = 2000
MAX_RELEASES = 5000
REVIEWED_YEARS = frozenset({2016, 2017})
NUMBER = re.compile(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,2})?\Z")


def element(parent, name):
    values = parent.findall(NS + name)
    if len(values) > 1:
        raise FetchError("PRTR duplicate field")
    return values[0] if values else None


def text(parent, name, required=True, limit=200):
    node = element(parent, name)
    if node is not None and len(node):
        raise FetchError("PRTR scalar field has children")
    value = (node.text or "").strip() if node is not None else ""
    if value in {"", "null"}:
        if required:
            raise FetchError("PRTR required field missing")
        return None
    if len(value) > limit:
        raise FetchError("PRTR field exceeds limit")
    return value


def flag(parent, name):
    value = text(parent, name)
    if value not in {"0", "1", "false", "true"}:
        raise FetchError("PRTR confidentiality flag invalid")
    return value in {"1", "true"}


def quantity(parent, name):
    node = element(parent, name)
    if node is not None and node.get("unitCode") != "KGM":
        raise FetchError("PRTR release unit is not reviewed KGM")
    value = text(parent, name, required=False, limit=64)
    if value is None:
        return None
    if not NUMBER.fullmatch(value):
        raise FetchError("PRTR quantity is not a bounded nonnegative decimal")
    number = Decimal(value)
    if not number.is_finite() or number > Decimal("1e30") or abs(number.as_tuple().exponent) > 30:
        raise FetchError("PRTR quantity exceeds numeric budget")
    return value


def xml_root(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
        raise FetchError("PRTR XML byte budget exceeded")
    try:
        decoded = body.decode("utf-8-sig")
        if "\x00" in decoded or "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
            raise FetchError("PRTR XML DTD, entities or alternate encoding refused")
        declaration = re.match(r"<\?xml\s+[^?]*\?>", decoded)
        if declaration:
            encoding = re.search(r'encoding\s*=\s*[\'"]([^\'"]+)[\'"]', declaration[0])
            if encoding and encoding[1].upper() != "UTF-8":
                raise FetchError("PRTR requires UTF-8")
        depth, count, root = 0, 0, None
        for event, node in ET.iterparse(io.StringIO(decoded), events=("start", "end")):
            if event == "start":
                depth += 1
                count += 1
                if root is None:
                    root = node
                if depth > MAX_DEPTH or count > MAX_NODES or not node.tag.startswith(NS):
                    raise FetchError("PRTR XML structure budget or namespace invalid")
            else:
                depth -= 1
        return root
    except (UnicodeError, ET.ParseError) as error:
        raise FetchError("PRTR malformed UTF-8 XML") from error


def parse_releases(body, year=2017):
    """Return whitelisted rows and diagnostics; not XSD validation or source approval.

    The operational caller must first verify the original's reviewed checksum.
    Decimal text and raw pollutant/method codes are intentionally preserved.
    """
    if year not in REVIEWED_YEARS:
        raise FetchError("PRTR year is outside reviewed originals")
    root = xml_root(body)
    if (
        root.tag != NS + "PollutantReleaseAndTransferReport"
        or text(root, "ReportingYear") != str(year)
        or text(root, "CountryID") != "PL"
        or text(root, "CoordinateSystemID") != "EPSG:4326"
    ):
        raise FetchError(f"PRTR report is outside reviewed PL{year} scope")
    facilities = root.findall(NS + "FacilityReport")
    if not 0 < len(facilities) <= MAX_FACILITIES:
        raise FetchError("PRTR facility budget exceeded or empty report")
    summary = {
        "scope": f"PL{year}_pollutant_releases_only",
        "reporting_year": year,
        "reporting_year_source": "report_header",
        "source_sha256": hashlib.sha256(body).hexdigest(),
        "source_bytes": len(body),
        "unit": "kg/year",
        "facilities_reported": len(facilities),
        "releases_reported": 0,
        "excluded_facilities": 0,
        "excluded_releases": 0,
        "skipped_pollutant_transfers": 0,
        "skipped_waste_transfers": 0,
        "missing_total": 0,
        "missing_accidental": 0,
        "xsd_validated": False,
        "pollutant_names_resolved": False,
    }
    rows, facility_ids, release_ids, media = [], set(), set(), Counter()
    for facility in facilities:
        releases = facility.findall(NS + "PollutantRelease")
        summary["releases_reported"] += len(releases)
        if summary["releases_reported"] > MAX_RELEASES:
            raise FetchError("PRTR release budget exceeded")
        summary["skipped_pollutant_transfers"] += len(facility.findall(NS + "PollutantTransfer"))
        summary["skipped_waste_transfers"] += len(facility.findall(NS + "WasteTransfer"))
        # Read both flags, even when the first is true; missing never implies public.
        confidential = flag(facility, "ConfidentialIndicator")
        protected = flag(facility, "ProtectVoluntaryData")
        if confidential or protected:
            summary["excluded_facilities"] += 1
            summary["excluded_releases"] += len(releases)
            continue
        if text(facility, "ReportingYear", required=False) not in {None, str(year)}:
            raise FetchError("PRTR facility year differs from report header")
        facility_id = text(facility, "NationalID", limit=100)
        if facility_id in facility_ids:
            raise FetchError("PRTR duplicate facility identity")
        facility_ids.add(facility_id)
        facility_name = text(facility, "FacilityName", required=False, limit=500)
        for release in releases:
            if flag(release, "ConfidentialIndicator"):
                summary["excluded_releases"] += 1
                continue
            medium = text(release, "MediumCode")
            method = text(release, "MethodBasisCode")
            pollutant = text(release, "PollutantCode", limit=100)
            if medium not in {"AIR", "WATER", "LAND"} or method not in {"M", "C", "E"}:
                raise FetchError("PRTR release medium or method outside reviewed codes")
            identity = (facility_id, medium, pollutant)
            if identity in release_ids:
                raise FetchError("PRTR duplicate release identity")
            release_ids.add(identity)
            total = quantity(release, "TotalQuantity")
            accidental = quantity(release, "AccidentalQuantity")
            summary["missing_total"] += total is None
            summary["missing_accidental"] += accidental is None
            rows.append(
                {
                    "facility_id": facility_id,
                    "facility_name": facility_name,
                    "reporting_year": year,
                    "reporting_year_source": "report_header",
                    "medium_code": medium,
                    "pollutant_code": pollutant,
                    "method_code": method,
                    "total_quantity": total,
                    "accidental_quantity": accidental,
                    "unit_code": "KGM",
                    "unit": "kg/year",
                }
            )
            media[medium] += 1
    summary["records"] = len(rows)
    summary["media_counts"] = dict(sorted(media.items()))
    return rows, summary


def read_verified_releases(job, now):
    """Use the existing file-integrity fence; no network or persistent parsed copy."""
    channel = job.channel
    if (
        channel.slug != profile.SLUG
        or channel.kind != "file"
        or channel.region != "PL"
        or channel.url != profile.URL
        or channel.config != profile.CONFIG
        or job.total_bytes != profile.SIZE
        or job.sha256 != profile.SHA256
    ):
        raise FetchError("PRTR local copy is not the reviewed original")
    stream = io.BytesIO()
    write_verified_file(job, now, stream)
    return parse_releases(stream.getvalue())
