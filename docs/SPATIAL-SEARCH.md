# Wyszukiwanie przestrzenne lokalnych danych PIG/MIDAS

Aktualizacja2026-10-04: [przecięcie rzeczywistej geometrii](SPATIAL-GEOMETRY.md)
jest osobnym trybem z topologią, otworami i rozłącznymi częściami. Poniższy opis
obwiedni dotyczy historycznego trybu envelope, zachowanego dla starych linków.
Nowy formularz proponuje geometry, a podpisane paczki przypinają metodę.
Shapely/NumPy dodane wyłącznie do obrazów Docker; wcześniejsza informacja
„brak nowych bibliotek” opisuje pierwotną wersję obwiedni, nie nowy tryb.

Aktualizacja2026-10-04T19:06Z: [strony trafień w paczce](SPATIAL-RESULT-PAGES.md),
oddzielne od nawigacji paczek kopii; eksport bieżącej strony i walidacja całej
paczki przy każdym żądaniu. Zachowano dotychczasowy ograniczony tryb bez paczek.

Aktualizacja2026-10-04: [przegląd w paczkach](SPATIAL-BATCHES.md) dodaje
podpisaną nawigację po lokalnych kopiach także dla dużego JCWPd, bez zwiększania
50MiB odczytu. Ustaw pole paczki na1; trafienia i eksport dotyczą wybranej
paczki i nadal podlegają limitowi1–500/5MiB, nie całego zbioru.
Poniższe historyczne wyniki jednorazowego odczytu pozostają prawdziwe.

Panel staff /data/spatial/ przeszukuje lokalne kopie przejrzanych przebiegów
pig_midas_scan i pig_spatial_scan. Nie jest nowym kolektorem, nie wykonuje HTTP,
nie wznawia przebiegów ani nie tworzy plików, zadań czy nowych kopii cache.
Motywacja: po katalogach i raportach pokrycia udostępniamy rzeczywistą selekcję
obiektów GIS i eksport, korzystając z już pozyskanych danych.

## Obsługa

1. Zaloguj się na istniejące konto operatora i otwórz /data/spatial/.
2. Wybierz zatwierdzony zbiór GeoJSON, np. hałdy lub GZWP.
3. Podaj prostokąt WGS84 (EPSG:4326): zachód, południe, wschód, północ.
   Domyślne wartości14/49/24.2/55 to przybliżony prostokąt obejmujący Polskę.
   Można podać mniejszy obszar. Kolejność współrzędnych: długość/szerokość.
4. Limit1–500 (domyślnie100). Wybierz Podgląd albo Plik GeoJSON.

Nie obsługujemy prostokątów przechodzących przez południk180°, punktów,
wartości NaN/Infinity, odwróconych ani zerowych zakresów. Zły formularz400.
Brak kopii, uszkodzenie, cofnięta akceptacja lub przekroczenie budżetu409;
nie udostępniamy częściowego wyniku po błędzie walidacji.

## Co znaczy wynik

Kryterium to przecięcie prostokąta z obwiednią geometrii, łącznie ze
stykiem granic. Nie jest to dokładne przecięcie poligonów: możliwe
wyniki nadmiarowe w otworach lub pomiędzy częściami MultiPolygon.
Nie przycinamy geometrii. Brak trafień w dostępnych kopiach nie
dowodzi braku obiektów w terenie ani kompletności danych dostawcy.
Nie potwierdzamy stanu prawnego, poprawności topologicznej czy
jednoczasowej wersji atrybutów/geometrii.

Całe dostępne strony (w trybie paczek: wszystkie wybrane strony) są
sprawdzane nawet po osiągnięciu limitu.
Matches_in_available_copies dotyczy trafień w sprawdzonych lokalnych
kopiach, returned to liczba zwrócona, truncated sygnalizuje limit.
Zakończony przebieg i pełna integralna retencja mogą potwierdzić
full_scope_verified dla przypiętego spisu OBJECTID, nie całej instytucji.
Niepełny przebieg lub wygasłe strony dają jawne ostrzeżenie częściowego
pokrycia; przeszukiwane są wyłącznie nadal dostępne kopie.

GeoJSON zawiera oryginalne pełne geometrie/atrybuty wybranych obiektów
oraz geoklimat_query: bbox, metodę, ID kanału/przebiegu, czas zapytania,
raport pokrycia, source attribution i receipts stron (ID, SHA256,
czas pobrania i wygaśnięcia). Metadane nie zawierają surowej konfiguracji
ani nowych połączeń do usług. Podgląd HTML ogranicza atrybuty do8pól/
200znaków i escapuje wartości. Eksport to5MiB maksymalnie,
application/geo+json, stała nazwa, nosniff, ETag odpowiedzi i no-store.
Nie jest to archiwum w cache ani dodanie trwałej kopii danych.

## Integralność i zasoby

Powtórne pobranie DataChannel i najnowszego przebiegu w PostgreSQL
REPEATABLE READ, READ ONLY. Kontrola access_approved, dokładnego
przejrzanego profilu PIG, retencji, sum SHA256, liczby rekordów,
monotonicznych OBJECTID i geometrii Polygon/MultiPolygon WGS84.
Przy pełnym spisie weryfikujemy identity_scope_hash. Metadataissues
zatrzymują zapytanie; callback stron działa wyłącznie po walidacji strony.

coverage_report zachowuje dotychczasowy interfejs, dodaje opcjonalny
on_page, max_payload_bytes i opcjonalny copy_ids do zweryfikowanego odczytu.
copy_ids jest ograniczony do25 kopii i wymaga walidacji z callbackiem.
Pozostałe
raporty i pełne eksporty pozostają zgodne.
Budżet wejściowy min(50MiB, DATA_MANUAL_MAX_BYTES), odczyt stron
jedna po drugiej; limit wyników nie pozwala pominąć pozostałej walidacji.
Nie zmieniamy cache15GB/retencji30dni/budżetów kolektorów.
Brak nowych bibliotek, PostGIS, migracji albo instalacji na hoście.

## Dowody

11focusedPASS1.732s/647fullPostgreSQLPASS23.917s/Ruff338/check/
nomigrations/buildwebPASS. Testy limitów, retencji, SHA256 także
po osiągnięciu limitu, błędów metadanych, budżetów, uprawnień,
GeoJSON bez częściowego wycieku, bbox i jawnej niedokładności obwiedni.

Odczyt2026-10-02T09:43–09:45Z, bbox14/49/24.2/55, limit10:
hałdy1386trafień/28stron/10wyników/49843B, GZWP140trafień/14stron/
10wyników/820478B; oba scope_verifiedTrue. Hałdy0.39s/GZWP0.37s.
MIDAS0/1/2:18141/6208/6034 trafień w tym prostokącie, po10wyników,
wszystkie full_scope_verifiedTrue; nie są to deklarowane całkowite
liczebności zbiorów poza prostokątem.
JCWPd odmowa Spatial query exceeds manual budget (kopie ponad50MiB):
bez częściowego wyniku i bez zwiększania limitu.

Przegląd większego P0: świeża próba WFS sdi.gdos.gov.pl nadal
Cannot confirm robots policy; nie utrwalono dodatkowego hosta,
nie obchodzono robots i nie aktywowano geometrii GDOŚ.
Oficjalny katalog:
https://www.gov.pl/web/gdos/dostep-do-danych-geoprzestrzennych
GDOŚ16/CRFOP i pełne4.5 pozostają otwarte; ta funkcja dotyczy danych PIG.

Końcowa próba 2026-10-02T09:46Z: formularz, podgląd i eksport wywołane
przez RequestFactory z istniejącym kontem staff zwróciły HTTP200.
GeoJSON:49843B, application/geo+json, nosniff i no-store.
Wszystkie5 usług działają, DBhealthy. Nie odświeżano workera ani nie
aktywowano kolektorów w ramach tego pakietu; nie zmieniono limitów cache.
