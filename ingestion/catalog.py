"""Legacy working channels. Master institutions/endpoints are now imported from the workbook."""

from ingestion.taxonomy import MASTER_TOPICS

TOPICS = {
    "inspire": ("INSPIRE i interoperacyjność", ["INSPIRE", "IIP", "GreenData4All"]),
    "gis": ("GIS i dane przestrzenne", ["GUGiK", "Geoportal", "WMS", "WFS", "EGiB"]),
    "monitoring": ("Monitoring i inspekcja", ["GIOŚ", "WIOŚ", "PMŚ", "kontrola"]),
    "climate": ("Klimat i adaptacja", ["klimat", "adaptacja", "emisje", "KOBiZE"]),
    "air": ("Powietrze", ["powietrze", "smog", "PM10", "PM2.5"]),
    "water": ("Wody", ["wod", "ściek", "powód", "susza"]),
    "nature": ("Przyroda i oceny oddziaływania", ["Natura 2000", "OOŚ", "przyrod"]),
    "waste": ("Odpady i gospodarka obiegu zamkniętego", ["odpady", "BDO", "GOZ"]),
    "law": ("Legislacja i konsultacje", ["ustawa", "rozporządzenie", "konsultacje"]),
    "funding": ("Finansowanie", ["nabór", "dofinansowanie", "FEnIKS", "LIFE"]),
    "data": ("Otwarte dane i usługi", ["API", "metadane", "otwarte dane", "HVD"]),
    "reporting": ("Obowiązki i sprawozdawczość", ["raport", "sprawozdanie", "termin"]),
}

GOV_CONFIG = {
    "row": ".art-prev li",
    "title": ".title",
    "link": "a[href]",
    "date": ".date",
    "summary": ".intro",
}
TOPICS.update({key: value for key, value in MASTER_TOPICS.items() if key not in TOPICS})
EC_CONFIG = {
    "row": ".ecl-content-item",
    "title": ".ecl-content-block__title",
    "link": ".ecl-content-block__title a",
    "date": "time",
    "summary": ".ecl-content-block__description",
}


def gov(slug, name, organization, path, topics):
    return dict(
        slug=slug,
        name=name,
        organization=organization,
        url="https://www.gov.pl/web/" + path,
        adapter="html",
        config=GOV_CONFIG,
        topics=topics,
        scope="Najnowsza strona listy; bez gwarancji pełnego archiwum.",
    )


CATALOG = [
    gov("mkis", "MKiŚ — wiadomości", "MKiŚ", "klimat/wiadomosci", ["climate", "law"]),
    gov("mkis-komunikaty", "MKiŚ — komunikaty", "MKiŚ", "klimat/komunikaty", ["reporting"]),
    gov("gios", "GIOŚ — aktualności", "GIOŚ", "gios/wiadomosci", ["monitoring", "air"]),
    gov("gugik", "GUGiK — wiadomości", "GUGiK", "gugik/wiadomosci", ["gis", "inspire", "data"]),
    gov("gdos", "GDOŚ — aktualności", "GDOŚ", "gdos/aktualnosci", ["nature", "law"]),
    gov("nfosigw", "NFOŚiGW — aktualności", "NFOŚiGW", "nfosigw/aktualnosci", ["funding"]),
    gov(
        "wody", "Wody Polskie — aktualności", "Wody Polskie", "wody-polskie/aktualnosci", ["water"]
    ),
    gov(
        "rdos-warszawa",
        "RDOŚ Warszawa — pilotaż regionalny",
        "RDOŚ Warszawa",
        "rdos-warszawa/aktualnosci",
        ["nature", "law"],
    ),
    gov(
        "cyfryzacja",
        "Ministerstwo Cyfryzacji — aktualności",
        "Ministerstwo Cyfryzacji",
        "cyfryzacja/aktualnosci",
        ["data"],
    ),
    gov(
        "infrastruktura",
        "Ministerstwo Infrastruktury — aktualności",
        "Ministerstwo Infrastruktury",
        "infrastruktura/aktualnosci",
        ["water", "law"],
    ),
    *[
        dict(
            slug=slug,
            name=name,
            organization="EEA",
            adapter="feed",
            language="en",
            url=f"https://www.eea.europa.eu/en/newsroom/rss-feeds/{path}/rss.xml",
            topics=topics,
            scope="Pozycje udostępnione w oficjalnym kanale RSS.",
        )
        for slug, name, path, topics in [
            ("eea-news", "EEA — komunikaty", "eeas-press-releases-rss", ["climate", "monitoring"]),
            ("eea-reports", "EEA — publikacje", "publications-rss", ["reporting"]),
        ]
    ],
    dict(
        slug="ec-environment",
        name="KE DG ENV — aktualności",
        organization="Komisja Europejska",
        url="https://environment.ec.europa.eu/news_en",
        adapter="html",
        config=EC_CONFIG,
        language="en",
        topics=["nature", "waste", "law"],
        scope="Pierwsza strona aktualności.",
    ),
    dict(
        slug="ec-climate",
        name="KE DG CLIMA — aktualności",
        organization="Komisja Europejska",
        url="https://climate.ec.europa.eu/news-other-reads/news_en",
        adapter="html",
        config=EC_CONFIG,
        language="en",
        topics=["climate", "law"],
        scope="Pierwsza strona aktualności.",
    ),
    *[
        dict(
            slug=f"eli-{journal.lower()}",
            name=f"ELI — {name}",
            organization="Sejm RP",
            url=f"https://api.sejm.gov.pl/eli/acts/{journal}/{{year}}",
            adapter="eli",
            topics=["law"],
            interval_minutes=360,
            scope=(
                "Bieżący i poprzedni rok, metadane wszystkich aktów. "
                "To surowa kolejka, bez klasyfikacji tematycznej."
            ),
        )
        for journal, name in [("DU", "Dziennik Ustaw"), ("MP", "Monitor Polski")]
    ],
    dict(
        slug="inspire-gugik",
        name="GUGiK — INSPIRE",
        organization="GUGiK",
        adapter="watch",
        url="https://www.gov.pl/web/gugik/inspire2",
        config={"content": "article"},
        topics=["inspire", "gis"],
        interval_minutes=1440,
        scope="Zmiana tekstu strony, nie stan prawny.",
    ),
    dict(
        slug="green-data",
        name="KE — Green Data / GreenData4All",
        organization="Komisja Europejska",
        url="https://environment.ec.europa.eu/law-and-governance/green-data_en",
        adapter="watch",
        config={"content": "main"},
        topics=["inspire", "data"],
        language="en",
        interval_minutes=1440,
        scope="Zmiana tekstu strony informacyjnej.",
    ),
    dict(
        slug="eurlex-inspire",
        name="EUR-Lex — procedura INSPIRE",
        organization="EUR-Lex",
        url="https://eur-lex.europa.eu/procedure/EN/2025_393",
        adapter="watch",
        config={"content": "#MainContent"},
        topics=["inspire", "law"],
        language="en",
        interval_minutes=360,
        scope="Obserwacja strony procedury; docelowo kanał EUR-Lex/Cellar.",
    ),
    dict(
        slug="rcl",
        name="RCL — projekty aktów",
        organization="RCL",
        url="https://legislacja.rcl.gov.pl/",
        adapter="watch",
        config={"content": "main"},
        topics=["law"],
        interval_minutes=360,
        scope=(
            "Kandydat: potrzebny adapter projektów; sama strona główna nie daje pokrycia procesu."
        ),
        manual_hold=True,
    ),
]
