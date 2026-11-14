"""User-approved bounded public search POSTs (LIFE, Horizon); never an arbitrary POST client."""

import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from django.conf import settings

from ingestion.transport import Client, FetchError, Response, validate_url

BASE = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
# LIFE approved 2026-10-01; Horizon Europe added by the user's card 2026-10-08T08:12Z.
# The Horizon programme id is UNCONFIRMED until the live probe on the user's PC.
# P4 2026-10-15: other EU programmes with environment, climate, energy or civil
# protection calls, each kept whole; programme ids read by the PC census (see
# docs/FUNDING-PROGRAMMES.md). "tolerant" profiles accept the portal's newer topic
# addresses, partnership call references and byte-identical duplicate rows.
# Census on the PC 2026-10-15T08:00Z (open/forthcoming EN topics, 1204 rows, 25 pages):
# each id below held only topics with its own identifier prefix.
PROGRAMMES = {
    "CEF": "43251567",  # Connecting Europe Facility (energy, transport): 11
    "DIGITAL": "43152860",  # Digital Europe: 81
    "EMFAF": "43392145",  # European Maritime, Fisheries and Aquaculture Fund: 1
    "I3": "44416173",  # Interregional Innovation Investments (ERDF): 9
    "JTM": "44773066",  # Just Transition Mechanism, public sector loan facility: 2
    "RENEWFM": "43253967",  # Renewable Energy Financing Mechanism: 1
}
PROFILES = {
    "life": {"frameworks": ("43252405",), "pages": 5, "tolerant": False},
    "horizon": {"frameworks": ("43108390",), "pages": 20, "tolerant": True},
    "programmes": {"frameworks": tuple(PROGRAMMES.values()), "pages": 10, "tolerant": True},
}


def query(profile="life"):
    if profile not in PROFILES:
        raise FetchError("Funding search profile outside reviewed scope")
    return {
        "bool": {
            "must": [
                {"terms": {"type": ["1", "2", "8"]}},
                {"terms": {"status": ["31094501", "31094502"]}},
                {"terms": {"frameworkProgramme": list(PROFILES[profile]["frameworks"])}},
                {"term": {"language": "en"}},
            ]
        }
    }


QUERY = query()


def request_spec(page, profile="life"):
    pages = PROFILES[profile]["pages"] if profile in PROFILES else 0
    if type(page) is not int or not 1 <= page <= pages:
        raise FetchError("Funding search page outside reviewed budget")
    url = (
        BASE
        + "?"
        + urlencode(
            {"apiKey": "SEDIA", "text": "***", "pageSize": 50, "pageNumber": page, "language": "en"}
        )
    )
    boundary = "GeoKlimatFundingSearch"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="query"; filename="query.json"\r\n'
        "Content-Type: application/json\r\n\r\n"
        + json.dumps(query(profile), separators=(",", ":"))
        + f"\r\n--{boundary}--\r\n"
    ).encode()
    return url, body, "multipart/form-data; boundary=" + boundary


class _SearchClient(Client):
    def __init__(self, parent, url, body, content_type):
        self.opener, self.robots, self.last_request = (
            parent.opener,
            parent.robots,
            parent.last_request,
        )
        self.search_url, self.body, self.content_type = url, body, content_type

    def _raw(self, url, headers=None, delay=1):
        if url != self.search_url:
            return super()._raw(url, headers, delay)
        validate_url(url)
        policy = self.robots["https://api.tech.ec.europa.eu"]
        if not policy.can_fetch(settings.INGEST_USER_AGENT, url):
            raise FetchError("robots.txt disallows funding search")
        origin = "api.tech.ec.europa.eu"
        time.sleep(max(0, self.last_request.get(origin, 0) + delay - time.monotonic()))
        self.last_request[origin] = time.monotonic()
        request = Request(
            url,
            data=self.body,
            method="POST",
            headers={
                "User-Agent": settings.INGEST_USER_AGENT,
                "Accept-Encoding": "identity",
                "Accept": "application/json",
                "Content-Type": self.content_type,
            },
        )
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            if 300 <= status < 400:
                raise FetchError("Funding search redirects are not permitted", status)
            chunks, size, started = [], 0, time.monotonic()
            while chunk := response.read(65536):
                size += len(chunk)
                if size > self.max_bytes or time.monotonic() - started > 30:
                    raise FetchError("Response exceeds size/time budget", status)
                chunks.append(chunk)
            return Response(
                status,
                b"".join(chunks),
                {key.lower(): value for key, value in response.headers.items()},
                url,
            )


def search(client, page, profile="life"):
    url, body, content_type = request_spec(page, profile)
    special = _SearchClient(client, url, body, content_type)
    _, delay = special._policy(url)
    response = special._request(url, delay=delay)
    if response.status != 200 or response.url != url:
        raise FetchError("Unexpected funding search response", response.status)
    return response
