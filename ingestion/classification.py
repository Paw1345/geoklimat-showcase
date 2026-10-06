"""Versioned editorial heuristics, not legal analysis or calibrated probabilities."""

import re

from django.utils import timezone

from ingestion.models import Classification
from ingestion.normalization import clean_text
from ingestion.taxonomy import ALIASES

RULE_VERSION = "rules-5-pilot"
# Deliberately separate from the loose source-discovery keywords in Topic.
RULES = {
    "inspire": [r"inspire", r"iip", r"greendata4all", r"interoperacyj\w*"],
    "gis": [
        r"gis",
        r"gugik",
        r"geoportal\w*",
        r"wms",
        r"wfs",
        r"egib",
        r"geodez\w*",
        r"geoinform\w*",
        r"przestrzenn\w*",
        r"geospatial",
        r"spatial data",
    ],
    "monitoring": [
        r"gioś",
        r"wioś",
        r"pmś",
        r"monitoring środowisk\w*",
        r"environmental monitoring",
    ],
    "climate": [
        r"klimat\w*",
        r"climate",
        r"emisj\w*",
        r"emissions?",
        r"kobize",
        r"dekarboniz\w*",
        r"decarboni[sz](?:ation|ing|ed|e|es)",
    ],
    "air": [r"powietrz\w*", r"smog\w*", r"pm10", r"pm2[.,]5", r"air quality"],
    "water": [
        r"wod(?:a|y|ę|ą|zie|ach|ami|n\w*)",
        r"wód",
        r"ściek\w*",
        r"powodzi\w*",
        r"susza",
        r"water",
        r"wastewater",
        r"flood\w*",
        r"drought",
    ],
    "nature": [
        r"natura 2000",
        r"ooś",
        r"przyrod\w*",
        r"bioróżnorod\w*",
        r"biodiversity",
        r"environmental impact",
        r"pollinators?",
        r"(?:threatened|endangered) species",
        r"species (?:could face|face|at risk of) extinction",
    ],
    "waste": [r"odpad\w*", r"bdo", r"goz", r"waste", r"circular economy", r"recykling\w*"],
    "data": [
        r"api",
        r"metadan\w*",
        r"metadata",
        r"otwart\w* dan\w*",
        r"open data",
        r"hvd",
        r"data sharing",
        r"data specifications?",
        r"udostępniani\w* danych",
        r"specyfikacj\w* danych",
    ],
    "law": [
        r"ustaw\w*",
        r"rozporządzen\w*",
        r"konsultac\w*",
        r"dyrektyw\w*",
        r"directive",
        r"regulation",
        r"consultation\w*",
    ],
    "funding": [r"nabór", r"dofinansowan\w*", r"feniks", r"funding", r"grant\w*"],
    # A published report is a document kind, not evidence of a reporting duty.
    "reporting": [
        r"sprawozdawczoś\w*",
        r"obowiąz\w* (?:raportowani\w*|sprawozdawcz\w*|składania sprawozdań)",
        r"termin\w* (?:składania|przekazania) (?:raport\w*|sprawozdań)",
        r"reporting (?:obligations?|requirements?|duties|deadlines?)",
        r"mandatory reporting",
    ],
}
RULES.update(
    {
        "emissions_ets_cbam": [r"ets", r"cbam", r"kobize", r"emisj\w*", r"emissions?"],
        "soil_geology": [
            r"geolog\w*",
            r"gleb\w*",
            r"soil",
            r"landslides?",
            r"osuwisk\w*",
            r"midas",
        ],
        "chemicals_pollution": [
            r"echa",
            r"reach",
            r"prtr",
            r"chemikali\w*",
            r"chemicals?",
            r"zanieczyszcze\w*",
        ],
        "energy": [r"oze", r"energi\w*", r"renewables?", r"offshore", r"fotowolta\w*"],
        "environmental_procedures": [
            r"ooś",
            r"sooś",
            r"eia",
            r"pozwoleni\w* środowisk\w*",
            r"environmental permits?",
        ],
        "technical_changes": [
            r"api change",
            r"zmian\w* api",
            r"outage",
            r"new dataset",
            r"schemat\w* danych",
        ],
    }
)
PATTERNS = {
    slug: [re.compile(r"(?<!\w)(?:" + pattern + r")(?!\w)", re.I) for pattern in patterns]
    for slug, patterns in RULES.items()
}
CORE = set(RULES) - {"law", "funding", "reporting", "data", "technical_changes"}
KINDS = [
    ("Konsultacje", r"konsultac\w*|consultation\w*"),
    (
        "Projekt",
        r"projekt\w* (?:ustaw\w*|rozporządzen\w*|dyrektyw\w*)|draft (?:law|regulation|directive)",
    ),
    ("Zmiana usługi", r"(?:zmian\w*|aktualizac\w*|nowe) (?:api|usług\w*)|api update"),
    ("Awaria", r"awari\w*|niedostępnoś\w*|outage"),
    ("Nabór", r"nabór|call for proposals"),
    ("Wytyczne", r"wytyczn\w*|guidance|guidelines"),
    # Noun inflections only: raportowanie/raportowy are not a published report.
    (
        "Raport",
        r"raport(?:u|owi|em|y|ów|om|ami|ach)?|raporcie|sprawozdani\w*|sprawozdań|reports?",
    ),
    ("Zbiór danych", r"zbiór danych|dataset"),
]
EXCLUSION = re.compile(
    r"(?<!\w)(?:nabór na stanowisko|ofert\w* pracy|job vacancies|job vacancy|"
    r"rekrutacja pracowników)(?!\w)",
    re.I,
)


def classify(item):
    title, summary = clean_text(item.title), clean_text(item.summary)
    evidence, topics, score = [], [], 0
    for slug, patterns in PATTERNS.items():
        for field, text, weight in (("title", title, 35), ("summary", summary, 20)):
            match = next((match for pattern in patterns if (match := pattern.search(text))), None)
            if match:
                topics.append(slug)
                score += weight
                evidence.append({"topic": slug, "field": field, "match": match.group(0)})
                break
    body = title + " " + summary
    kind = "Publikacja aktu" if item.source.adapter == "eli" else "Komunikat"
    if item.source.adapter != "eli":
        for label, pattern in KINDS:
            if re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", body, re.I):
                kind = label
                break
    state = "relevant" if CORE.intersection(topics) else "review"
    if "data" in topics and re.search(
        r"otwart\w* dan\w*|open data|metadata|metadan\w*|\bhvd\b", body, re.I
    ):
        state = "relevant"
    exclusion = EXCLUSION.search(title)
    if exclusion:
        state = "excluded"
        evidence.append({"rule": "recruitment", "field": "title", "match": exclusion.group(0)})
    if not topics:
        evidence.append(
            {
                "rule": "no_topic_match",
                "explanation": "Brak dopasowania nie dowodzi braku trafności.",
            }
        )
    elif state == "review":
        evidence.append(
            {"rule": "context_required", "explanation": "Ogólny termin wymaga oceny kontekstu."}
        )
    topics += [ALIASES[slug] for slug in list(topics) if slug in ALIASES]
    return dict(
        topics=topics,
        kind=kind,
        score=min(score, 100),
        auto_state=state,
        evidence=evidence,
        rule_version=RULE_VERSION,
        item_revision=item.revision,
    )


def classify_and_save(item):
    values = classify(item)
    previous = Classification.objects.filter(item=item).first()
    if previous and all(getattr(previous, key) == value for key, value in values.items()):
        return False
    Classification.objects.update_or_create(
        item=item, defaults={**values, "classified_at": timezone.now()}
    )
    return True
