# Duże lokalne zbiory GIS — przegląd w paczkach

Aktualizacja2026-10-04T19:06Z: [strony trafień](SPATIAL-RESULT-PAGES.md)
pozwalają teraz przejrzeć dalsze dopasowania wewnątrz tej samej paczki.
Eksport dotyczy bieżącej strony trafień; brak automatycznego scalania paczek.

## Cel i obsługa

Panel staff `/data/spatial/` obsługuje zatwierdzone lokalne przebiegi
PIG/MIDAS (`pig_midas_scan`, `pig_spatial_scan`). JCWPd przekracza limit
jednego odczytu; paczki pozwalają przeglądać zapisane strony bez jego zwiększania.
Nie ma nowych kolektorów, żądań do dostawców, plików cache ani zadań pobierania.

1. Zaloguj się istniejącym kontem operatora i otwórz `/data/spatial/`.
2. Wybierz zbiór, prostokąt WGS84 i mały limit wyników. Dla JCWPd zacznij od1.
3. W polu „Paczka kopii” wpisz1 i wybierz podgląd.
4. Używaj „Następna paczka”, „Poprzednia paczka” lub eksportu tej paczki.
5. Przy zmianie filtrów/limitu rozpocznij nowy przegląd. Jeśli stary token
   został wysłany ze zmienionymi filtrami, panel odmawia i oferuje nowy przegląd.
   Ta czynność nie resetuje kolektora, danych, kursorów ani cache.

Przykład lokalny:
`/data/spatial/?channel=16&west=14&south=49&east=24.2&north=55&limit=1&format=html&copy_page=1`.
ID16 jest lokalnym kanałem JCWPd, nie stałą gwarancją ID po odtworzeniu bazy.
Puste pole paczki zachowuje wcześniejszy odczyt całego dostępnego zakresu.

## Zakres, integralność i limity

- Każde żądanie pracuje w PostgreSQL `REPEATABLE READ, READ ONLY`.
  Token jest podpisany, ważny do1godziny, wiąże kanał/przebieg, profil,
  akceptację/atrybucję, bbox, limit, budżet, rozmiar paczek oraz manifest kopii.
  Nie jest uprawnieniem dostępu: nadal wymagane są konto staff i akceptacja kanału.
- Manifest obejmuje lokalne niewygasłe kopie dostępne przy pierwszym żądaniu,
  z identyfikatorami, SHA256, rozmiarami, czasami, typem i metadanymi stron.
  Limit10000 kopii. Nowy przebieg nie podmienia starego; nowe kopie, również
  dodane później z wcześniejszą datą pobrania, nie poszerzają przypiętego zakresu.
- Paczka zawiera maksymalnie25 kolejnych kopii i `min(50MiB, DATA_MANUAL_MAX_BYTES)`.
  SQL `OCTET_LENGTH(payload)` sprawdza rzeczywiste długości przed pobraniem
  payloadów. Rozbieżność z deklarowanym rozmiarem, wygaśnięcie przypiętej kopii
  lub zmiana manifestu zatrzymuje cały wynik, zamiast po cichu zmniejszać zakres.
- Pełne payloady są czytane po jednej stronie. SHA256, schemat, liczby rekordów,
  provenance, monotoniczne OBJECTID i geometrie są sprawdzane dla każdej strony
  wybranej paczki, także po osiągnięciu limitu wyników. Integralność payloadów
  innych paczek nie jest w tym żądaniu sprawdzana; uszkodzenie tej samej długości
  przy niezmienionym zapisanym SHA wykryje dopiero odczyt odpowiedniej paczki.
- Wynik pozostaje ograniczony do500 obiektów/5MiB. Eksport dotyczy tylko
  zwróconych obiektów wybranej paczki, nie wszystkich dopasowań tej paczki.
  Jeśli limit ucina wynik, `truncated=true`; strony trafień pozwalają teraz
  przeglądać dalsze obiekty tej paczki. Paczki nadal nie są globalnym eksportem.
  Zmniejsz bbox lub limit przy zbyt dużym wyniku.
- `matches_scope=selected_copy_batch`, `global_match_count=null`.
  Historyczne pole `matches_in_available_copies` w tym trybie oznacza trafienia
  wyłącznie w sprawdzonej paczce. `copy_batch` podaje numer/liczbę paczek,
  rozmiary, anchor i hash manifestu, a `receipts` wskazują sprawdzone strony.
  Globalny raport pokrycia metadanych nie potwierdza SHA innych paczek.
- `full_scope_verified` jest false dla podzbioru kopii. Może być true tylko,
  gdy jedna paczka obejmuje cały dostępny, kompletny przebieg i zweryfikowano
  również cały przypięty spis tożsamości. Przejście osobno przez wszystkie
  paczki nie nadaje automatycznie globalnego dowodu hash/unikalności OBJECTID.
- Prostokąt przecina obwiednię, nie dokładne poligony. Brak clip/maski PL/UE,
  dowodu topologii/stanu prawnego lub jednoczasowego snapshotu dostawcy.
  Token nie blokuje normalnego prune i nie przedłuża30dni retencji.

Błędy parametrów400, niedostępne kopie/token/integralność/budżet409,
bez częściowego GeoJSON. Eksport `application/geo+json`, rzeczywisty ETag SHA256,
`nosniff`, `no-store`; błędy nie ujawniają tokenów lub surowej konfiguracji.
Nie zmieniono cache15GB, kolektorów, bibliotek, migracji ani workera.

## Dowody — 2026-10-04T18:52–18:55Z

15 nowych testów; 39 focused PASS6.685s; pełne1001 PostgreSQL PASS60.346s.
Ruff check/format463 plików, Django check, brak migracji, build web/worker PASS.
Testy: rozłączność paczek, limit bajtów, podpis/wygaśnięcie tokena, zmiana
filtrów/profilu/budżetu, zmiana/wygaśnięcie manifestu, nowe przebiegi/kopie,
rzeczywiste długości, uszkodzenie wybranej i niewybranej strony, uprawnienia,
nawigacja/eksport/nowy zakres, odmowa po przekroczeniu limitu wynikowego.

Rzeczywisty JCWPd16:18 kopii,56101231B; cały odczyt nadal odmówiony.
Paczka1:15 kopii/51442622B,150 trafień obwiedni,1 wynik/117678B.
Paczka2:3 kopie/4658609B,24 trafienia obwiedni,1 wynik/285247B.
Obie `verified_available`, `full_scope_verified=false`, globalny count null;
receipts rozłączne, obejmują strony1–18. Nie jest to dowód174 unikalnych obiektów
ani globalnej integralności spisu. Liczniki kopii/przebiegów/zadań bez zmian,
HTTP wykluczone przez mock podczas lokalnej próby.
Istniejący staff: HTML200, GeoJSON200/ETag/nosniff/no-store. Rzeczywisty
anonimowy GET302 do logowania, `/health/ready/`200; DBhealthy.
Worker bez restartu, start nadal2026-10-04T18:07:58.051880826Z/RestartCount0.
Pierwsza próba diagnostyczna miała błędną nazwę modelu zadań; skorygowano
na istniejący `DownloadJob`, bez zmian bazy. Początkowe lint poprawione formatem.

Pełny epik4.5 pozostaje otwarty: paczki nie zamykają brakujących konektorów,
maski przestrzennej, licencji/pobierania dużych danych ani odbioru biznesowego.
Aktualizacja2026-10-04: [SPATIAL-GEOMETRY](SPATIAL-GEOMETRY.md) dodaje
metodę geometry oraz tokenv2 przypinający metodę. Tokenv1 pozostaje wyłącznie
dla envelope; zmiana metody wymaga nowego przeglądu. Reszta budżetów,
manifestu, retencji i zakresu lokalnego dowodu bez zmian.
