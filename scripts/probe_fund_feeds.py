"""Read-only inspection of canonical WFOŚiGW sites, never automatic activation."""

import json
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ingestion.transport import Client

URLS = [
    "https://wfosigw.pl/",
    "https://wfosigw.bialystok.pl/",
    "https://bip.wfosigw.rzeszow.pl/aktualnosci",
]


def probe(url):
    try:
        response = Client().get(url)
        soup = BeautifulSoup(response.body, "html.parser")
        return {
            "url": response.url,
            "feeds": [dict(node.attrs) for node in soup.select('link[rel="alternate"]')],
            "examples": [
                str(node)[:1800] for node in soup.select("article, .item, .news-item")[:2]
            ],
        }
    except Exception as exc:
        return {"url": url, "error": str(exc)[:200]}


with ThreadPoolExecutor(max_workers=3) as pool:
    for result in pool.map(probe, URLS):
        print(json.dumps(result, ensure_ascii=False), flush=True)
