# PIG MIDAS — geometrie i atrybuty

Aktualizacja2026-10-01T09:17+02:00: osobny pełny skan złóż zakończony18147/18147,
182kopie32930146B/fullspis+payloady verified07:17Z. Nie zmienia próbki100/dzień
ani nie dowodzi jednoczasowych atrybutów/pełnego zakresu PIG. PIG-PAGING.md.

Aktualizacja2026-10-01T00:30+02:00: panel i scalony eksport ukończonych
PIGprzebiegów opisuje [DATA-COVERAGE](DATA-COVERAGE.md). Próbki pozostają próbkami;
complete odnosi się do przypiętego spisu, nie pełnej obsługi PIG.

Aktualizacja 2026-09-30T21:23Z: dodano trzy aktywne kanały pełniejszych przebiegów
z przypiętym spisem ID i wznawianiem; po 600 obiektów, nadal running.
[PIG-PAGING](PIG-PAGING.md) opisuje aktualny mechanizm i ograniczenia.
Próbki poniżej zachowane; wcześniejszy następny krok paginacji jest zrealizowany,
ale ukończenie rzeczywistych przebiegów jeszcze nie jest potwierdzone.

Stan2026-09-30T20:35Z:3kolektory aktywne po zgodzie, configure dry/apply,
audytach i łagodnym odświeżeniu wyłącznie workera. Pełne luki pozostają.

| Kanał | Kopia lokalna | Liczba u dostawcy | Zapis |
|---|---|---|---|
| pig-midas-0-sample |100 złóż |18147 |182065B |
| pig-midas-1-sample |100 obszarów górniczych |6214 |127900B |
| pig-midas-2-sample |100 terenów górniczych |6038 |126807B |

Razem300geometrii/atrybutów,3kopie436772B. Pierwsze100według OBJECTID ASC,
nie losowa próba i nie pełny zbiór; interwał1440min. Źródło krajowe MIDAS,
bez dokładnej maski granic Polski. Dostawca przelicza geometrie do EPSG:4326.
Daty/atrybuty zachowane bez wnioskowania o ważności decyzji lub granic.
Kontrola liczby przed/po odczycie odrzuca zmieniony count; nie gwarantuje
zamrożonej wersji danych przy równoległych zmianach o tej samej liczbie obiektów.

Źródła pierwotne:
[MIDAS dane](https://www.pgi.gov.pl/midas/dane-przestrzenne.html),
[ArcGIS Query GET](https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/),
[warunki PIG](https://www.pgi.gov.pl/rejestry-i-bazy-danych-geologicznych/zasady-korzystania-z-isp.html),
[regulamin/ograniczenia MIDAS](https://www.pgi.gov.pl/midas/zrodla-danych.html).
Warunki PIG edytowane16września2026. Opis jest parafrazą zakresu, nie opinią
prawną lub stwierdzeniem jednolitej otwartej licencji.

ingestion/pig_data.py:3stałe REST MapServer0/1/2/query, dokładny config/kind/PL,
GET-only przez istniejący Client, robots/DNS/redirect/budżet/quota zachowane.
Bez arbitralnego SQL/URL/załączników/kolejnych stron. HTTP200error/HTML odrzucane.
FeatureCollection<=100, rosnące unikalne id=OBJECTID, Polygon/MultiPolygon,
zamknięte pierścienie i skończone współrzędne WGS84,<=100000punktów/obiekt.
Cała próbka walidowana przed zapisem. geojson/mode pig_midas_sample, brak migracji.

Retencja30dni to ważność lokalnej kopii, nie wiek złoża/obszaru. Identyczne dane
zwracają unchanged, bez odnowienia fetched_at/expiry. Eksport/preview wykluczają
wygasłe kopie przed czyszczeniem. Wspólna quota15GB i wstrzymana migracjaD: bez zmian.
GeoJSON zawiera geoklimat_provenance: źródło, URL, warunki, zakres przetworzenia,
source_count/partial; czas wytworzenia null. Eksport dodaje zapisany czas pozyskania
i wygaśnięcia kopii, read-only. ETag liczony dla eksportu większego w tej próbie
o111B niż payload w cache; załącznik pozostaje binarny z rozszerzeniem.json.
UI pokazuje pokrycie100/count i ostrzega o częściowej próbce.

configure_pig_data dry-run domyślny/--apply, transakcja+advisorylock, początkowo
disabled/unapproved, zachowanieadminconfig. audit_data --source SLUG, po sukcesie
--enable; collect_data --source SLUG --force dry-run, --apply zapisuje.
Live:3audyty100/activation3/first3stored/repeat3unchanged,po1kopii/kanał.
Kanały9/10/11 i kopie14139/14140/14141. Workerexit0at20:32:53Z/resumed20:35:40Z.
Image/sourceparserSHA256=55f596aeb3cc1a22b7817d358a5a414fdcc16565ff359592ee96b634d576aee5.
12focused/377fullPGtestsPASS15.371s,Ruff217/check/nomigrations/buildPASS.
GETtrzechkart/eksportów/ready200,ETag/provenance/100features sprawdzone,5usługrunning.
Geośrodowisko64 WMS haldy:4opisy/import4created/repeat4unchanged,niepełnae-MGśP.
189Sources140active49disabled,11DataChannels;fullneeds_connector36/37/62–65/34gaps.
Następny zakres: pełniejsze paginowane pobieranie z wznawianiem/tożsamością,
oddzielnie rzeczywiste SOPO/hydro/Geośrodowisko i duże pliki na żądanie.
