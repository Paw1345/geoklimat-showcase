"""GET-only discovery of two remaining official fund news lists."""

import json
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ingestion.transport import Client


def probe(url):
    try:
        response = Client().get(url)
        soup = BeautifulSoup(response.body, "html.parser")
        return {
            "url": response.url,
            "feeds": [dict(n.attrs) for n in soup.select('link[rel="alternate"]')],
            "rows": [str(n)[:6000] for n in soup.select(".views-row, article, .news-item")[:2]],
            "news_links": [
                (n.get_text(" ", strip=True), n.get("href"))
                for n in soup.select("a[href]")
                if "aktualno" in n.get("href", "")
            ][:8],
        }
    except Exception as exc:
        return {"url": url, "error": str(exc)[:200]}


with ThreadPoolExecutor(max_workers=2) as pool:
    for result in pool.map(
        probe,
        [
            "https://wfosigw.torun.pl/aktualnosci?field_tags_target_id=All",
            "https://www.wfosigw.wroclaw.pl/",
        ],
    ):
        print(json.dumps(result, ensure_ascii=False), flush=True)
