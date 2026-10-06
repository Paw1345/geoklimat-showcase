# DG CLIMA — Areas of action (rejestr 167)

Oficjalny indeks: https://climate.ec.europa.eu/areas-action_en.
Pierwotny endpoint https://climate.ec.europa.eu/ kieruje do wyboru języka,
nie do danych polityk. Oficjalny odsyłacz English prowadzi do index_en,
a menu tej strony wskazuje Areas of action. Nie zmieniono hostu transportu.

Zakres: wszystkie karty pięciu sekcji indeksu:
Strategies & Governance, Mitigation, Adaptation & Resilience,
Funding & Innovation, Cooperation & Engagement. Świeża strona ma 22 karty
(6+8+2+2+4). Nie pobieramy podstron, aktów, mediów ani pełnych dokumentów.
Nie twierdzimy, że to pełny katalog prawa lub pokrycie wszystkich jego zmian.

Tytuł, kategoria i krótki opis pochodzą z karty. Opis ma 1–600 znaków,
bez generowania lub domyślnego uzupełniania. Katalog nie podaje dat:
published/updated pozostają nieznane. Cel 2050 lub inny rok w opisie nie
staje się datą publikacji. Opisy zachowujemy tak, jak podaje je źródło,
bez potwierdzania ich aktualności, poprawności czy stanu prawnego.
Nie poprawiamy samodzielnie powtórzonego opisu źródłowego kart F-gases/Ozone.
UI oznacza opis jako tekst karty, nie niezależnie sprawdzoną treść.
funding_call_confirmed=False: karta finansowania nie jest otwartym naborem.

## Walidacja i linki

Dokładny profil/endpoint, UTF-8, title/h1/main i pięć znanych sekcji.
1–50 kart/sekcję i <=150 łącznie, żadnych kart spoza przypisanych sekcji.
Pojedynczy title-link i description; wszystkie rekordy zweryfikowane przed partią.
Brak powtórzonych URL; URL jest stabilną tożsamością niezależnie od tytułu/kolejności.
Zmiany opisu i kategorii aktualizują rekord.

Dopuszczone są bezquery/fragmentów bezpośrednie karty areas-action/*_en
oraz cztery konkretne oficjalne odsyłacze zapisane w EXTERNAL_LINKS:
Green Deal, Clean Industrial Deal, EU Covenant of Mayors, European Climate Pact.
Są to wyłącznie odsyłacze z katalogu. Nie dodano hostów do transportowej
allowlisty ani robots wyjątków; nie wysyłamy żądań do zewnętrznych stron.
Nowy odsyłacz lub inny schemat wymaga ponownego przeglądu, nie fallbacku.

configure_clima_policies: domyślnie dry/noHTTP, wspólny lock+transaction.
Zamienia wyłącznie nieużywane wyłączone registry-167 z pierwotnym profilem.
PK/admin/history/linkage/owner/name/240min zachowane; repeat zachowuje audyt
i zgodę. Workbook/cache/schema/kursory bez zmian. Po testach: configdry/apply/
repeat, świeży audit, dryimport i dryactivation przed apply/import/repeat.
Worker odświeżany tylko SIGTERM/naturalexit0, bez resetu/SIGKILL.

## Dowody

2026-10-01: official GET200/parser22/0; 10focusedPASS0.363s.
Testy obejmują 5sekcji, opisy/daty nieznane, zewnętrzne linki bez childGET,
tożsamość/zmianę opisu, strukturę, budżety/duplikaty, endpoint/config oraz
dry/apply/repeat/admin/history/linkage/UIescaping.
2026-10-01T20:48Z: 10focused/566full PostgreSQL PASS20.175s,
Ruff check/format306files, Django check/no migrations/buildPASS.
Configure dry busy/nochange, then worker SIGTERM naturalexit0,
configdry/apply/repeat zachował PK127/admin/linkage/240min.
Fresh audit200/22/0, dry import22/0 i eligible1dry przed apply.
Import22created; niezależny drugi HTTP200:22unchanged/0created/0updated.
22published/source_updatedNULL/fail0, sourceactiveapproved.
Worker resumed20:47:39Z/unless-stopped, manifest
cc604bd758d460ca644288d302b365e7e41819dc4eba0060bc41d6caad893030.
5running/DBhealthy, ready+registry167HTTP200, Item14998 UI źródłowego opisu/
unknown dates verified.196Sources163active33disabled/cache15GB bez zmian.
Pełne34luki biznesowe nadal otwarte, nie mylić z liczbą disabledSources.
DG ENV165 rootselectlanguage/Englishindex sprawdzony, nie aktywowany.
Hydroportal27 root200->index.html stronaISOK, brak kanału feed/news;
nie zinterpretowano strony głównej jako danych hydrologicznych.
