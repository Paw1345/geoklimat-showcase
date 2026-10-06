"""Read-only discovery of official GIOS API resource metadata, not measurements."""

import json
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError

from ingestion.gios_catalogue import DATASETS
from ingestion.transport import Client, FetchError

BASE = "https://api.dane.gov.pl/1.4/datasets"


def discover(client):
    channels = []
    for dataset, registry in DATASETS.items():
        dataset_url = f"{BASE}/{dataset}"
        response = client.get(dataset_url)
        data = json.loads(response.body)["data"]
        if (
            response.url != dataset_url
            or data.get("type") != "dataset"
            or data.get("id") != str(dataset)
            or data.get("relationships", {}).get("institution", {}).get("data")
            != {"type": "institution", "id": "100"}
        ):
            raise CommandError("GIOS dataset identity or institution changed")
        expected = data["relationships"]["resources"]["meta"]["count"]
        if type(expected) is not int or not 0 < expected <= 100:
            raise CommandError("GIOS resource count outside discovery budget")
        url = f"{dataset_url}/resources?per_page=100"
        response = client.get(url)
        page = json.loads(response.body)
        rows = page["data"]
        if (
            response.url != url
            or not isinstance(rows, list)
            or len(rows) != expected
            or page["meta"]["count"] != expected
            or page.get("links", {}).get("next")
        ):
            raise CommandError("GIOS resources incomplete or schema changed")
        resources, seen = [], set()
        for row in rows:
            key, attrs = row.get("id"), row.get("attributes", {})
            link, title = attrs.get("link"), attrs.get("title")
            if not isinstance(link, str):
                raise CommandError("GIOS resource link missing")
            parsed = urlsplit(link)
            if (
                row.get("type") != "resource"
                or not isinstance(key, str)
                or not key.isascii()
                or not key.isdigit()
                or key in seen
                or not isinstance(title, str)
                or not title.strip()
                or parsed.scheme != "https"
                or parsed.netloc != "dane.gios.gov.pl"
                or not parsed.path.startswith("/api/")
                or parsed.fragment
            ):
                raise CommandError("GIOS resource identity or endpoint changed")
            seen.add(key)
            resources.append({"resource_id": key, "title": title, "url": link})
        channels.append({"registry_id": registry, "dataset_id": dataset, "resources": resources})
    # Only inspect access policy; do not fetch resources when policy is unconfirmed.
    try:
        response = client.get("https://dane.gios.gov.pl/robots.txt")
        access = {"status": "policy_confirmed", "http": response.status}
    except FetchError as exc:
        access = {"status": "blocked", "reason": str(exc), "http": exc.status}
    return {
        "scope": "official_catalogue_metadata_only",
        "portal_registry_id": 6,
        "channels": channels,
        "access": access,
        "resources_fetched": False,
        "database_writes": False,
    }


class Command(BaseCommand):
    help = "Read-only GIOS discovery via dane.gov.pl; no activation or resource downloads."

    def handle(self, *args, **options):
        try:
            report = discover(Client())
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError("GIOS catalogue schema changed") from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False))
