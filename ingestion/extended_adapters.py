import hashlib
import json
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError


def metadata_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def snapshot_content(source, soup):
    selector = source.config.get("content") if source.adapter == "watch" else None
    if selector:
        matches = soup.select(selector)
        if len(matches) != 1:
            raise FetchError("Configured snapshot content missing or ambiguous")
        main = matches[0]
        # gov.pl articles wrap both prose and section navigation/publication metrics.
        if urlsplit(source.url).hostname == "www.gov.pl" and main.name == "article":
            content = main.select(".editor-content")
            if len(content) != 1:
                raise FetchError("gov.pl article body missing or ambiguous")
            main = content[0]
        return main
    return soup.select_one("main, [role=main], article, #MainContent")


def json_rows(source, body):
    data = json.loads(body)
    if source.config.get("mode") == "sejm":
        if not isinstance(data, list):
            raise FetchError("Sejm list schema changed")
        return data
    if source.config.get("mode") == "gios":
        if not isinstance(data, dict) or not isinstance(data.get("Lista stacji pomiarowych"), list):
            raise FetchError("GIOS v1 station schema changed")
        return data["Lista stacji pomiarowych"]
    raise FetchError("JSON profile not configured")


def parse_extended(source, response):
    if source.config.get("mode") == "bdl_collections":
        from ingestion.bdl_catalogue import parse_catalogue

        return parse_catalogue(source, response)
    if "eea_press" in source.config:
        from ingestion.eea_press import parse_press

        return parse_press(source, response)
    if "bdpn_plan" in source.config:
        from ingestion.bdpn_plan import parse_plan

        return parse_plan(source, response)
    if "opn_documents" in source.config:
        from ingestion.opn_documents import parse_documents

        return parse_documents(source, response)
    if "opn_news" in source.config:
        from ingestion.opn_news import parse_news

        return parse_news(source, response)
    if "bdpn_news" in source.config:
        from ingestion.bdpn_news import parse_news

        return parse_news(source, response)
    if "environment_policies" in source.config:
        from ingestion.environment_policies import parse_policies

        return parse_policies(source, response)
    if "clima_policies" in source.config:
        from ingestion.clima_policies import parse_policies

        return parse_policies(source, response)
    if "cbd_updates" in source.config:
        from ingestion.cbd_updates import parse_updates

        return parse_updates(source, response)
    if "unep_news" in source.config:
        from ingestion.unep_news import parse_news

        return parse_news(source, response)
    if "ipcc_reports" in source.config:
        from ingestion.ipcc_reports import parse_reports

        return parse_reports(source, response)
    if "gus_publications" in source.config:
        from ingestion.gus_publications import parse_publications

        return parse_publications(source, response)
    if source.config.get("mode") == "gios_stations_v1":
        from ingestion.gios_stations import parse_stations

        return parse_stations(source, response)
    if "mkis_journal" in source.config:
        from ingestion.mkis_journal import parse_register

        return parse_register(source, response)
    if "inspire_oeil" in source.config:
        from ingestion.inspire_oeil import parse_procedure

        return parse_procedure(source, response)
    if "wfos_news" in source.config:
        from ingestion.wfos_news import parse_listing

        return parse_listing(source, response)
    if "wios_bydgoszcz" in source.config:
        from ingestion.wios_bydgoszcz import parse_listing

        return parse_listing(source, response)
    if source.adapter == "ogc" and source.config.get("mode") == "pig_wms":
        from ingestion.pig_services import parse_capabilities

        return parse_capabilities(source, response)
    if source.adapter == "web" and source.config.get("listing") == "gdos_services":
        from ingestion.gdos_services import parse_directory

        return parse_directory(source, response)
    if source.adapter == "json":
        entries = []
        for row in json_rows(source, response.body):
            if source.config["mode"] == "sejm":
                key = str(row.get("number") or "")
                title = row.get("title", "")
                url = source.url.rstrip("/") + "/" + quote(key, safe="")
                summary = row.get("description", "")
                published, updated = row.get("documentDate", ""), row.get("changeDate", "")
            else:
                key = str(row.get("Identyfikator stacji") or "")
                title = row.get("Nazwa stacji", "")
                url = "https://powietrze.gios.gov.pl/pjp/current/station_details/info/" + quote(
                    key, safe=""
                )
                summary, published, updated = "Metadane stacji GIOŚ; nie bieżący pomiar.", "", ""
            if not key or not title:
                raise FetchError("Required JSON record identity missing")
            entries.append(
                Entry(
                    key,
                    title,
                    url,
                    summary,
                    published,
                    updated,
                    {
                        "record_kind": "metadata",
                        "document_hash": metadata_hash(row),
                        **(
                            {
                                "legislation": {
                                    field: row[field]
                                    for field in (
                                        "number",
                                        "term",
                                        "ELI",
                                        "processStartDate",
                                        "closureDate",
                                        "passed",
                                    )
                                    if field in row
                                }
                            }
                            if source.config["mode"] == "sejm"
                            else {}
                        ),
                    },
                )
            )
        return entries
    if source.adapter == "stac":
        data = json.loads(response.body)
        if not isinstance(data.get("collections"), list):
            raise FetchError("Expected STAC collections")
        if any(link.get("rel") == "next" for link in data.get("links", [])):
            raise FetchError("STAC pagination needs explicit configuration")
        entries = []
        for row in data["collections"]:
            key = row["id"]
            entries.append(
                Entry(
                    key,
                    row.get("title") or key,
                    source.url.rstrip("/") + "/" + quote(key, safe=""),
                    row.get("description", ""),
                    metadata={
                        "record_kind": "dataset_collection",
                        "document_hash": metadata_hash(row),
                        "license": row.get("license", ""),
                    },
                )
            )
        return entries
    if source.adapter == "ogc":
        if b"<!DOCTYPE" in response.body.upper() or b"<!ENTITY" in response.body.upper():
            raise FetchError("XML entities rejected")
        root = ET.fromstring(response.body)
        if root.tag.split("}")[-1] not in {
            "WMS_Capabilities",
            "WMT_MS_Capabilities",
            "WFS_Capabilities",
            "Capabilities",
        }:
            raise FetchError("Not an OGC capabilities document")
        labels = list(
            dict.fromkeys(
                node.text.strip()
                for node in root.iter()
                if node.tag.split("}")[-1] in {"Title", "Name", "Identifier"}
                and node.text
                and node.text.strip()
            )
        )
        if not labels:
            raise FetchError("OGC capabilities have no service metadata")
        return [
            Entry(
                source.url,
                source.name,
                source.url,
                "; ".join(labels)[:600],
                metadata={
                    "record_kind": "service_capabilities",
                    "document_hash": hashlib.sha256(ET.tostring(root)).hexdigest(),
                    "service": source.config.get("service"),
                    "version": root.attrib.get("version"),
                    "names_count": len(labels),
                },
            )
        ]
    soup = BeautifulSoup(response.body, "html.parser")
    for node in soup.select("script,style,nav,footer"):
        node.decompose()
    if "wios_lodz" in source.config:
        from ingestion.wios_lodz import parse_listing

        return parse_listing(source, soup, response)
    if source.config.get("regional_listing"):
        from ingestion.regional_profiles import parse_regional

        return parse_regional(source, soup, response)
    if source.config.get("listing"):
        from ingestion.public_listings import parse_listing

        return parse_listing(source, soup, response)
    # A listing is recognized only by known structural containers, not all page links.
    rows = soup.select(".art-prev li, .ecl-content-item, article.post")
    entries = []
    for row in rows:
        link = row.select_one(
            ".title a[href], a.ecl-content-block__title[href], "
            ".ecl-content-block__title a[href], h2 a[href], h3 a[href]"
        )
        if not link:
            continue
        url = urljoin(response.url, link["href"])
        date = row.select_one("time, .date")
        summary = row.select_one(".intro, .ecl-content-block__description, .entry-summary")
        entries.append(
            Entry(
                url,
                link.get_text(" ", strip=True),
                url,
                summary.get_text(" ", strip=True) if summary else "",
                date.get("datetime", date.get_text(" ", strip=True)) if date else "",
                metadata={"record_kind": "news"},
            )
        )
    if entries:
        return entries
    main = soup.select_one("main, [role=main], article, #MainContent")
    body_text = text(str(main)) if main else ""
    if len(body_text) < 100:
        raise FetchError("No readable main content; JS or site-specific adapter required")
    lowered = body_text.casefold()
    if any(
        marker in lowered
        for marker in ("verify you are human", "access denied", "captcha", "enable javascript")
    ):
        raise FetchError("Access challenge or JS application; no content ingested")
    return [
        Entry(
            source.url,
            source.name,
            response.url,
            body_text[:600],
            metadata={
                "record_kind": "page_snapshot",
                "document_hash": hashlib.sha256(body_text.encode()).hexdigest(),
            },
        )
    ]
