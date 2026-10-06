"""Read-only inspection of explicitly selected public thematic sections."""

import json
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ingestion.transport import Client

URLS = {
    84: "https://www.wios.lublin.pl/aktualnosci/",
    126: "https://kujawsko-pomorskie.pl/tag/srodowisko/",
    128: "https://lubuskie.pl/wiadomosci/kategoria/87",
    129: "https://www.lodzkie.pl/srodowisko",
    130: "https://www.malopolska.pl/dla-mieszkanca/srodowisko",
    135: "https://pomorskie.eu/srodowisko/",
    137: "https://www.swietokrzyskie.pro/category/urzad-marszalkowski/departamenty/departament-rolnicta-klimatu-i-srodowiska/",
    138: "https://warmia.mazury.pl/srodowisko/aktualnosci",
    139: "https://www.umww.pl/kategoria/aktualnosci-korzystania-i-informacji-o-srodowisku",
}


def inspect(entry):
    key, url = entry
    try:
        r = Client().get(url)
        soup = BeautifulSoup(r.body, "html.parser")
        feeds = [(t.get("title"), t.get("href")) for t in soup.select('link[rel="alternate"]')]
        nodes = soup.select(
            "main article, article.post, .news, .news-item, .item, .row h2, h2 a, h3 a"
        )
        return {
            "id": key,
            "url": r.url,
            "feeds": feeds,
            "examples": [str(t)[:1600] for t in nodes[:3]],
        }
    except Exception as exc:
        return {"id": key, "error": str(exc)[:200]}


with ThreadPoolExecutor(max_workers=4) as executor:
    for result in executor.map(inspect, URLS.items()):
        print(json.dumps(result, ensure_ascii=False), flush=True)
