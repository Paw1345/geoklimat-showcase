"""JRC dataset files on staff demand (registry 181, user card 2026-10-08T12:07Z).

One bounded SPARQL GET lists a catalogued ENVI dataset's distributions with their
licence. Only a single file under CC BY 4.0 on an allowlisted HTTPS host can become a
file channel; the bytes come later through the shared Range downloader with its 50 MiB
per-file limit. Nothing is fetched automatically and the catalogue source is untouched.
"""

import hashlib
import json
import re
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.db import transaction

from ingestion.jrc_catalogue import DATASET, URL
from ingestion.models import DataChannel, RegistryEntry
from ingestion.transport import FetchError

REGISTRY_ID = 181
MAX_DISTRIBUTIONS = 50
FIELDS = [
    "distribution",
    "download",
    "access",
    "license",
    "dataset_license",
    "format",
    "size",
    "title",
]
CC_BY = {
    "http://creativecommons.org/licenses/by/4.0",
    "https://creativecommons.org/licenses/by/4.0",
    "http://creativecommons.org/licenses/by/4.0/legalcode",
    "https://creativecommons.org/licenses/by/4.0/legalcode",
    "http://publications.europa.eu/resource/authority/licence/CC_BY_4_0",
}
ATTRIBUTION = (
    "European Commission, Joint Research Centre (JRC), CC BY 4.0. Plik pobrany na "
    "żądanie bez zmian; opis i warunki na stronie zbioru: "
)


def query(dataset_uri):
    return f"""PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
SELECT ?distribution ?download ?access ?license ?dataset_license ?format ?size ?title WHERE {{
 <{dataset_uri}> a dcat:Dataset; dcat:distribution ?distribution .
 OPTIONAL {{ ?distribution dcat:downloadURL ?download }}
 OPTIONAL {{ ?distribution dcat:accessURL ?access }}
 OPTIONAL {{ ?distribution dct:license ?license }}
 OPTIONAL {{ <{dataset_uri}> dct:license ?dataset_license }}
 OPTIONAL {{ ?distribution dct:format ?format }}
 OPTIONAL {{ ?distribution dcat:byteSize ?size }}
 OPTIONAL {{ ?distribution dct:title ?title }}
}} ORDER BY ?distribution LIMIT {MAX_DISTRIBUTIONS * 4 + 1}"""


def value(binding, kinds=("uri",)):
    if binding is None:
        return None
    if (
        not isinstance(binding, dict)
        or binding.get("type") not in (*kinds, "typed-literal")
        or not isinstance(binding.get("value"), str)
        or not binding["value"].strip()
        or len(binding["value"]) > 1500
    ):
        raise FetchError("JRC distribution binding changed")
    return binding["value"].strip()


def normal_licence(uri):
    return uri.rstrip("/") if uri else None


# Live 2026-10-08: some downloadURL values are HTML directory listings (path ending in
# "/") or JRC document pages, so only a path naming a data file with one of these
# extensions is offered.
DATA_EXTENSIONS = {
    "7z", "csv", "geojson", "gpkg", "gz", "json", "nc", "parquet", "tif", "tiff", "tsv",
    "txt", "xls", "xlsx", "xml", "zip",
}  # fmt: skip


def is_data_file(url):
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return "." in name and name.rsplit(".", 1)[-1].lower() in DATA_EXTENSIONS


def file_url_allowed(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in settings.INGEST_ALLOWED_HOSTS
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
    )


def distributions(client, dataset_uri):
    if not DATASET.fullmatch(dataset_uri or ""):
        raise FetchError("JRC dataset identity outside catalogue")
    url = (
        URL
        + "?"
        + urlencode({"query": query(dataset_uri), "format": "application/sparql-results+json"})
    )
    response = client.get(url, headers={"Accept": "application/sparql-results+json"})
    if response.status != 200 or response.url != url:
        raise FetchError("JRC left reviewed endpoint or unexpected status")
    try:
        data = json.loads(response.body)
        rows = data["results"]["bindings"]
        valid = data["head"]["vars"] == FIELDS and isinstance(rows, list)
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError("JRC JSON schema changed") from exc
    if not valid or len(rows) > MAX_DISTRIBUTIONS * 4:
        raise FetchError("JRC distribution list outside budget")
    grouped = {}
    for row in rows:
        if not isinstance(row, dict) or "distribution" not in row or set(row) - set(FIELDS):
            raise FetchError("JRC distribution row changed")
        key = value(row["distribution"], ("uri", "bnode"))
        item = grouped.setdefault(
            key,
            {"urls": set(), "licences": set(), "formats": set(), "sizes": set(), "titles": set()},
        )
        for name in ("download", "access"):
            if row.get(name):
                item["urls"].add((name, value(row[name])))
        for name in ("license", "dataset_license"):
            if row.get(name):
                item["licences"].add((name, normal_licence(value(row[name]))))
        if row.get("format"):
            item["formats"].add(value(row["format"], ("uri", "literal")))
        if row.get("size"):
            item["sizes"].add(value(row["size"], ("literal",)))
        if row.get("title"):
            item["titles"].add(value(row["title"], ("literal",)))
    if len(grouped) > MAX_DISTRIBUTIONS:
        raise FetchError("JRC distribution list outside budget")
    result = []
    for key in sorted(grouped):
        item = grouped[key]
        downloads = sorted(u for kind, u in item["urls"] if kind == "download")
        file_url = downloads[0] if len(downloads) == 1 else None
        own = {lic for kind, lic in item["licences"] if kind == "license"}
        licences = own or {lic for kind, lic in item["licences"] if kind == "dataset_license"}
        licence = next(iter(licences)) if len(licences) == 1 else None
        size = None
        if len(item["sizes"]) == 1 and re.fullmatch(r"\d{1,15}", next(iter(item["sizes"]))):
            size = int(next(iter(item["sizes"])))
        reasons = []
        if not file_url:
            reasons.append("brak jednego adresu pliku (downloadURL)")
        elif not file_url_allowed(file_url):
            reasons.append("host pliku poza listą zatwierdzonych")
        elif not is_data_file(file_url):
            reasons.append("adres nie wskazuje pliku danych (katalog lub strona)")
        if licence not in CC_BY:
            reasons.append("licencja inna niż CC BY 4.0 lub niepodana")
        if size is not None and size > settings.DATA_MANUAL_MAX_BYTES:
            reasons.append("plik większy niż 50 MiB")
        result.append(
            {
                "distribution": key,
                "file_url": file_url,
                "access_urls": sorted(u for kind, u in item["urls"] if kind == "access"),
                "licence": licence,
                "format": ", ".join(sorted(item["formats"]))[:300],
                "size_bytes": size,
                "title": sorted(item["titles"])[0][:300] if item["titles"] else "",
                "eligible": not reasons,
                "reasons": reasons,
            }
        )
    return result


def slug_for(file_url):
    return "jrc-file-" + hashlib.sha256(file_url.encode()).hexdigest()[:16]


def file_channel(dataset_uri, dataset_title, distribution):
    """Create or reuse the approved file channel for one eligible distribution."""
    if not distribution["eligible"]:
        raise FetchError("JRC file not eligible for on-demand download")
    url = distribution["file_url"]
    config = {
        "mode": "jrc_file",
        "dataset": dataset_uri,
        "distribution": distribution["distribution"],
        "license": distribution["licence"],
    }
    with transaction.atomic():
        registry = RegistryEntry.objects.filter(registry_id=REGISTRY_ID).first()
        if not registry:
            raise FetchError("Import the registry first")
        channel = DataChannel.objects.select_for_update().filter(slug=slug_for(url)).first()
        if channel:
            if (channel.kind, channel.url, channel.config, channel.region) != (
                "file",
                url,
                config,
                "EU",
            ):
                raise FetchError("Preserving administrator file channel configuration")
            return channel
        page = "https://data.jrc.ec.europa.eu/dataset/" + DATASET.fullmatch(dataset_uri)[1]
        label = distribution["title"] or url.rsplit("/", 1)[-1]
        return DataChannel.objects.create(
            registry=registry,
            slug=slug_for(url),
            name=("JRC — " + dataset_title[:120] + " — " + label[:60])[:200],
            kind="file",
            url=url,
            config=config,
            region="EU",
            attribution=ATTRIBUTION + page,
            # The user's card approved single CC BY files up to 50 MiB on demand.
            access_approved=True,
            enabled=False,
        )


def audit_file(client, channel):
    """One 1 KiB Range request for an existing JRC file channel; no job, no full fetch.

    Confirms the reviewed URL still answers on an allowlisted host and that size/ETag match
    the last complete local copy, so a not_tested on-demand channel gets an access status.
    """
    from ingestion.downloads import file_limit, validated_chunk
    from ingestion.models import DownloadJob

    if (
        channel.kind != "file"
        or channel.config.get("mode") != "jrc_file"
        or channel.registry.registry_id != REGISTRY_ID
        or channel.slug != slug_for(channel.url)
        or not file_url_allowed(channel.url)
        or not is_data_file(channel.url)
        or normal_licence(channel.config.get("license")) not in CC_BY
    ):
        raise FetchError("Not a reviewed JRC file channel")
    response = client.get(channel.url, headers={"Range": "bytes=0-1023"}, allow_partial=True)
    if response.url != channel.url:
        raise FetchError("JRC file left the reviewed URL")
    total, etag = validated_chunk(DownloadJob(offset=0), response)
    if response.status not in (200, 206) or total > file_limit(channel):
        raise FetchError("JRC file outside the per-file limit")
    copy = (
        DownloadJob.objects.filter(channel=channel, status="complete")
        .order_by("-created_at", "-pk")
        .first()
    )
    if copy and (total != copy.total_bytes or (copy.etag and etag != copy.etag)):
        raise FetchError("JRC file changed since the last complete copy")
    return {
        "status": "verified",
        "http": response.status,
        "total_bytes": total,
        "sample_bytes": len(response.body),
        "etag": etag,
        "matches_complete_job": copy.pk if copy else None,
        "scope": "file_access_only_not_content_validation",
    }
