# Opcjonalny zakres rekordów — 2026-09-30T15:35Z

Pakiet użyteczności po audycie; nie odbiór ekspercki A2/A3/A4.
Dodano pole Zakres rekordów na stronie głównej i w wyszukiwarce.

## Użycie i granice

- Wszystkie rekordy (brak parametru lub pusty): dotychczasowe wyniki i reguły.
- Publikacje: `?record_scope=publications`, bez rozpoznanych katalogów danych/usług.
- Katalogi: `?record_scope=catalogues`, wyłącznie rozpoznane metadane katalogowe.

Zakres katalogowy definiują istniejące adaptery i pole metadata.record_kind:
metadata, dataset_collection, service_directory, service_capabilities.
Nie klasyfikujemy po tytule, instytucji, słowie GIS ani ocenie trafności.
Publikacje zachowują brak typu, JSONnull, nieznany przyszły typ, news/news_notice,
funding_call, technical_document, page_snapshot i publication_metadataCellar.
To techniczny podział rekordów, nie wykrywanie samych zmian prawa ani priorytetów.
Nadal mogą występować wydarzenia, historyczne dokumenty i wpisy Dooceny/Wykluczone.

Strona główna zachowuje dotychczasową regułę ELI: akty wymagają istniejącego
filtru źródła/rejestru/tematu/oceny/rodzaju; sam Zakres rekordów jej nie znosi.
Wyszukiwarka obejmuje również akty ELI. Surowe pomiary/GIS/pliki nadal są w/data/,
nie w tym filtrze katalogu publikacji. Nie usunięto danych i nie zmieniono klasyfikacji.

Filtr łączy się z pozostałymi filtrami, frazą, kolejnością i paginacją;
dotychczasowe ograniczenia dat/brakujących opisów pozostają bez zmian.
Zawężenie następuje przed wyborem reprezentanta grupy duplikatów:
niepasujący rekord główny nie ukrywa pasującego członka.
Nieznana wartość daje jawny błąd formularza/HTTP400, nie cichy powrót do wszystkich.
GET tylko odczytuje bazę; brak HTTP do wydawców i zapisów ocen/preferencji.

## Weryfikacja i dalszy odbiór

8 nowych/26 celowanych/352 wszystkie testy PostgreSQL PASS; Ruff200 lint/formatPASS,
Django checkPASS i makemigrationsnodrift. Testy obejmują typy/brak typu/JSONnull,
przyszły typ, domyślne wyniki, ręczną ocenę, wspólne filtry, reprezentanta grupy,
paginację, ELI, invalidscope, POST405 i tylko odczyt/noHTTP.

Lokalne GET2026-09-30: pięć widoków HTTP200 z nowym polem.
Snapshot grup: home6028, homepublikacje1215, homekatalogi4813;
searchpublikacje6671 i searchkatalogi4813. To stan tej bazy, nie stały KPI;
różnice home/search wynikają m.in. z zachowanej reguły ELI i reprezentacji grup.
Nie oznacza wyższej trafności, pełnego pokrycia ani odbioru w przeglądarce.

Następny krok A2: specjalista porównuje wyniki na stałej próbce30 i wybiera
przydatny widok. A3: zalogowany scenariusz; A4: poprawki na podstawie oceny.
Zmiana domyślnego strumienia wymaga osobnej decyzji; na razie pozostaje bez zmian.
