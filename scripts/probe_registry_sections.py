"""Read-only endpoint discovery; candidates never imply approval or configuration.

Run with manage.py shell -c "exec(open('scripts/probe_registry_sections.py').read())".
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ingestion.models import RegistryEntry
from ingestion.transport import Client, FetchError

PATTERN = re.compile(
    r"środow|srodow|klimat|geodez|geoinform|przestrzen|aktualno|wiadomo|news", re.I
)


def probe(entry):
    key, url = entry
    # Explicitly test HTTPS candidate; never follow an insecure HTTP response.
    url = url.replace("http://", "https://", 1)
    try:
        response = Client().get(url)
        soup = BeautifulSoup(response.body, "html.parser")
        if any(
            marker in soup.get_text(" ", strip=True).casefold()
            for marker in (
                "incapsula incident",
                "verify you are human",
                "access denied",
                "request unsuccessful",
            )
        ):
            return {"id": key, "status": "access_challenge"}
        links = {}
        for link in soup.select("a[href]"):
            target = urljoin(response.url, link["href"])
            title = link.get_text(" ", strip=True)
            if (
                PATTERN.search(title)
                and urlsplit(target).netloc == urlsplit(response.url).netloc
                and urlsplit(target).scheme == "https"
            ):
                links[target] = title[:160]
        return {
            "id": key,
            "status": "candidates_only",
            "url": response.url,
            "links": list(links.items())[:25],
        }
    except Exception as exc:
        return {
            "id": key,
            "status": "failed",
            "error": str(exc)[:200] if isinstance(exc, FetchError) else type(exc).__name__,
        }


entries = [
    (entry.registry_id, entry.master["Endpoint URL"])
    for entry in RegistryEntry.objects.filter(integration_state="needs_endpoint")
]
with ThreadPoolExecutor(max_workers=4) as executor:
    for result in executor.map(probe, entries):
        print(json.dumps(result, ensure_ascii=False), flush=True)
