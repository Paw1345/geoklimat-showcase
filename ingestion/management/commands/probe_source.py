"""Read-only diagnostics for a reviewed public HTML/OGC endpoint."""

import json
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand, CommandError

from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Inspect a bounded public response through the regular robots/HTTPS transport."

    def add_arguments(self, parser):
        parser.add_argument("url")
        parser.add_argument("--selector", default="main, article, h1, h2, #content")

    def handle(self, *args, **options):
        try:
            response = Client().get(options["url"])
        except FetchError as exc:
            raise CommandError(str(exc)) from exc
        if b"Capabilities" in response.body[:1000]:
            if b"<!DOCTYPE" in response.body.upper() or b"<!ENTITY" in response.body.upper():
                raise CommandError("XML entities rejected")
            root = ET.fromstring(response.body)
            self.stdout.write(
                json.dumps(
                    [
                        (n.tag, n.text)
                        for n in root.iter()
                        if n.tag.split("}")[-1] in {"Name", "Value", "DefaultCRS"}
                    ][:70],
                    ensure_ascii=False,
                )
            )
            return
        soup = BeautifulSoup(response.body, "html.parser")
        rows = []
        for node in soup.select(options["selector"])[:12]:
            rows.append(
                {
                    "tag": node.name,
                    "id": node.get("id"),
                    "class": node.get("class"),
                    "text": node.get_text(" ", strip=True)[:350],
                    "links": [
                        (a.get_text(" ", strip=True)[:100], a.get("href"))
                        for a in node.select("a[href]")[:8]
                    ],
                    "parent": str(node.parent)[:400],
                }
            )
        self.stdout.write(json.dumps({"url": response.url, "nodes": rows}, ensure_ascii=False))
