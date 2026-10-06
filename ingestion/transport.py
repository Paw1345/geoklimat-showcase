"""Bounded GET-only transport. No credentials, arbitrary hosts or silent redirects."""

import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from django.conf import settings

from ingestion.sopo_policy import ERROR as SOPO_ERROR
from ingestion.sopo_policy import blocked_url as sopo_blocked_url


class FetchError(Exception):
    def __init__(self, message, status=None, retry_after=0, url=None, redirect_url=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.url = url
        self.redirect_url = redirect_url


class ProviderPolicyBlocked(FetchError):
    """Provider usage conditions, not a transient connection fault."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict
    url: str
    access_policy: str = "allowed-or-404"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Cellar's official API origin delegates only robots.txt to its owning OP portal.
# Reviewed against the live301 and official Cellar documentation on2026-09-30.
REVIEWED_ROBOTS_REDIRECTS = {
    "https://publications.europa.eu/robots.txt": "https://op.europa.eu/robots.txt",
}

GIOS_STATIONS_URL = "https://api.gios.gov.pl/pjp-api/v1/rest/station/findAll"
GIOS_STATIONS_REQUEST = GIOS_STATIONS_URL + "?page=0&size=500"
GIOS_ROBOTS_URL = "https://api.gios.gov.pl/robots.txt"
GIOS_BROKEN_ROBOTS_TARGET = "http://www.gios.gov.pl/"
GIOS_POLICY_EXCEPTION = "approved-gios-stations-api-exception-2026-10-01"
EMS_ACTIVATIONS_REQUEST = (
    "https://mapping.emergency.copernicus.eu/activations/api/activations/?limit=20&offset=0"
)


def is_clms_catalogue_request(url):
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "stac.dataspace.copernicus.eu"
        and parsed.path == "/v1/collections"
        and params.get("q") == ["clms"]
        and params.get("limit") == ["100"]
    )


class GiosStationPolicy:
    """User-approved API terms exception; never a cached origin-wide robots allow."""

    def can_fetch(self, user_agent, url):
        return url == GIOS_STATIONS_REQUEST

    def crawl_delay(self, user_agent):
        return 1


def validate_url(url):
    if sopo_blocked_url(url):
        raise ProviderPolicyBlocked(SOPO_ERROR)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in settings.INGEST_ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise FetchError("URL outside approved HTTPS sources")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise FetchError("Non-public destination rejected")


def retry_delay(value):
    try:
        return min(86400, max(0, int(value)))
    except (ValueError, TypeError):
        try:
            return min(
                86400,
                max(0, int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())),
            )
        except (ValueError, TypeError, OverflowError):
            return 0


def request_timeout(url):
    """Allow more time only for reviewed slow WFS and Copernicus STAC searches."""
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    if (
        parsed.scheme == "https"
        and parsed.netloc == "stac.dataspace.copernicus.eu"
        and parsed.path == "/v1/search"
    ):
        return 30
    if (
        parsed.scheme == "https"
        and parsed.netloc == "mapy.geoportal.gov.pl"
        and parsed.path == "/wss/service/wfsGN/guest"
        and params.get("SERVICE") == ["WFS"]
        and params.get("VERSION") == ["2.0.0"]
        and params.get("REQUEST") == ["GetFeature"]
        and params.get("TYPENAMES") == ["gn:NamedPlace"]
        and len(params.get("STARTINDEX", [])) == 1
        and params["STARTINDEX"][0].isascii()
        and params["STARTINDEX"][0].isdigit()
    ):
        return 60
    return 15


class Client:
    max_bytes = 5 * 1024 * 1024

    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.robots = {}
        self.last_request = {}

    def _raw(self, url, headers=None, delay=1):
        for _ in range(5):
            validate_url(url)
            origin = urlsplit(url).netloc
            policy = self.robots.get(f"https://{origin}")
            if policy and not policy.can_fetch(settings.INGEST_USER_AGENT, url):
                raise FetchError("robots.txt disallows redirect destination")
            time.sleep(max(0, self.last_request.get(origin, 0) + delay - time.monotonic()))
            self.last_request[origin] = time.monotonic()
            request = Request(
                url,
                headers={
                    "User-Agent": settings.INGEST_USER_AGENT,
                    "Accept-Encoding": "identity",
                    **(headers or {}),
                },
                method="GET",
            )
            try:
                response = self.opener.open(request, timeout=request_timeout(url))
            except HTTPError as exc:
                response = exc
            with response:
                status = response.code
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                if status in (301, 302, 303, 307, 308):
                    target = urljoin(url, response_headers.get("location", ""))
                    if url == "https://ecds.ecmwf.int/api/catalogue/v1/collections":
                        raise FetchError(
                            "ECMWF collection API redirect rejected",
                            status,
                            3600,
                            url=url,
                            redirect_url=target,
                        )
                    if is_clms_catalogue_request(url):
                        raise FetchError(
                            "CLMS collection API redirect rejected",
                            status,
                            3600,
                            url=url,
                            redirect_url=target,
                        )
                    if url == EMS_ACTIVATIONS_REQUEST:
                        raise FetchError(
                            "EMS activation API redirect rejected",
                            status,
                            3600,
                            url=url,
                            redirect_url=target,
                        )
                    if url == GIOS_STATIONS_REQUEST:
                        raise FetchError(
                            "GIOS station API redirect rejected",
                            status,
                            3600,
                            url=url,
                            redirect_url=target,
                        )
                    if (
                        urlsplit(target).netloc != origin
                        and REVIEWED_ROBOTS_REDIRECTS.get(url) != target
                    ):
                        raise FetchError(
                            "Cross-host redirect requires catalog review",
                            status,
                            url=url,
                            redirect_url=target,
                        )
                    url = target
                    continue
                chunks = []
                size = 0
                start = time.monotonic()
                while chunk := response.read(65536):
                    size += len(chunk)
                    if size > self.max_bytes or time.monotonic() - start > 30:
                        raise FetchError("Response exceeds size/time budget", status)
                    chunks.append(chunk)
                return Response(status, b"".join(chunks), response_headers, url)
        raise FetchError("Too many redirects")

    def _request(self, url, headers=None, delay=1):
        for attempt in range(3):
            try:
                result = self._raw(url, headers, delay)
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    raise FetchError("Network connection or timeout failure") from exc
                time.sleep(2**attempt)
                continue
            if result.status not in (429, 500, 502, 503, 504):
                return result
            wait = retry_delay(result.headers.get("retry-after"))
            if wait > 5 or attempt == 2:
                raise FetchError("Remote service unavailable", result.status, wait)
            time.sleep(max(wait, 2**attempt))
        raise FetchError("Retry budget exhausted")

    def _policy(self, url, gios_station_exception=False):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            try:
                response = self._request(origin + "/robots.txt")
            except FetchError as exc:
                if not (
                    gios_station_exception
                    and url == GIOS_STATIONS_REQUEST
                    and exc.status == 302
                    and exc.url == GIOS_ROBOTS_URL
                    and exc.redirect_url == GIOS_BROKEN_ROBOTS_TARGET
                ):
                    raise
                return GiosStationPolicy(), 31
            rules = RobotFileParser()
            if response.status == 404:
                rules.parse(["User-agent: *", "Allow: /"])
            elif (
                response.status == 200
                and "<html" not in response.body[:200].decode("utf-8", errors="ignore").lower()
            ):
                rules.parse(response.body.decode("utf-8", errors="replace").splitlines())
            else:
                raise FetchError("Cannot confirm robots policy", response.status)
            self.robots[origin] = rules
        rules = self.robots[origin]
        if not rules.can_fetch(settings.INGEST_USER_AGENT, url):
            raise FetchError("robots.txt disallows this path")
        delay = max(1, rules.crawl_delay(settings.INGEST_USER_AGENT) or 1)
        if delay > 30:
            raise FetchError("robots crawl-delay requires manual schedule review")
        if parsed.hostname == "api.gios.gov.pl":
            delay = max(delay, 31)
        return rules, delay

    def funding_search(self, page):
        from ingestion.funding_search_transport import search

        return search(self, page)

    def get(self, url, headers=None, allow_partial=False, gios_station_exception=False):
        if sopo_blocked_url(url):
            raise ProviderPolicyBlocked(SOPO_ERROR)
        if gios_station_exception and (url != GIOS_STATIONS_REQUEST or allow_partial or headers):
            raise FetchError("GIOS station exception outside approved request")
        rules, delay = self._policy(url, gios_station_exception)
        if gios_station_exception:
            # No retry storm: one API attempt per refresh, including429/network failures.
            try:
                response = self._raw(url, delay=delay)
            except (URLError, TimeoutError, OSError) as exc:
                raise FetchError("Network connection or timeout failure", retry_after=3600) from exc
            if response.url != url:
                raise FetchError("GIOS station API redirect rejected", response.status)
            if response.status in (429, 500, 502, 503, 504):
                raise FetchError(
                    "Remote service unavailable",
                    response.status,
                    max(3600, retry_delay(response.headers.get("retry-after"))),
                )
            if isinstance(rules, GiosStationPolicy):
                response.access_policy = GIOS_POLICY_EXCEPTION
        else:
            response = self._request(url, headers, delay)
        if response.url != url and not rules.can_fetch(settings.INGEST_USER_AGENT, response.url):
            raise FetchError("Redirect destination disallowed by robots")
        accepted = (200, 206) if allow_partial else (200, 304)
        if response.status not in accepted:
            raise FetchError(
                "Unexpected HTTP response",
                response.status,
                3600 if gios_station_exception else 0,
            )
        return response
