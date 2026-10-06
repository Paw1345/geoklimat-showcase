# STAC — katalog scen i GeoJSON dla GIS

[STACbounded](STAC-BOUNDED.md) jest dostępny przez ten sam katalog lokalnych
paczek i ich limity; profile20/21 aktywne, GeoJSON36/60obiektów potwierdzony.
UEdalejrunning/wynikdatowany. Zakres24h/bounded
nie zapewnia dokładnej maski ani pełnej doby i nie podnosi limitu eksportu.

[Audyt wielopaczkowy](STAC-AUDIT.md) pozwala sprawdzić unikalność całego
przypiętego manifestu. Katalog poniżej nadal pokazuje wystąpienia jednej paczki,
bez automatycznego deduplikowania wyników na podstawie audytu.

Pakiet 4.5.8/4.5.9: staff-only `/data/stac/scenes/`, lokalna projekcja danych
z [weryfikatora paczek](STAC-INTEGRITY.md). Nie pobiera scen, rastrów ani assets,
nie wykonuje HTTP, zapisów do bazy, resetów ani zmian harmonogramów/retencji.

## Jak korzystać

1. Zaloguj się kontem staff i otwórz `/data/stac/`.
2. Przy wybranym przebiegu wybierz „Katalog scen i eksport GeoJSON pierwszej paczki”.
   Kanał i przebieg zostają przypięte w adresie; nie zgaduj ich numerów.
3. Opcjonalnie podaj dokładny identyfikator sceny, zakres dat obserwacji lub bbox.
4. Po sprawdzeniu wyników wybierz eksport GeoJSON albo następną paczkę.

Filtry i numer przebiegu pozostają w linku następnej paczki. Jeśli przebieg jest
w toku, jego liczba stron może wzrosnąć; każda odpowiedź opisuje własny spójny
odczyt bazy, nie jeden zamrożony stan całej sesji przeglądania. Wygasanie danych
może uniemożliwić ponowny odczyt. Nie przedłużamy retencji na potrzeby eksportu.

Parametry GET:

| Parametr | Znaczenie |
| --- | --- |
| `channel`, `harvest` | Wymagane numery kanału STAC i należącego do niego przebiegu |
| `start` | Pierwsza strona paczki, 1–10000; domyślnie 1 |
| `limit` | Maksymalna liczba wyników, 1–250; domyślnie 100 |
| `scene` | Dokładne ID sceny, maks. 200 znaków; pusty oznacza wszystkie |
| `since`, `until` | Granice włączne, ISO 8601 z czasem i jawną strefą, np. `2026-10-01T00:00:00Z` |
| `bbox` | Opcjonalne W,S,E,N w WGS84, np. `14.12,49,24.15,54.84` |
| `format` | `html` (domyślnie) lub `geojson` |

Odwrócone daty, daty bez strefy, NaN/Infinity, niepoprawny bbox/przejście przez
180°, limit i format są odrzucane: 400. Niedostępna kopia, niewłaściwy kanał
lub przebieg, brak zatwierdzonego dostępu, problem integralności/retencji lub
przekroczenie budżetu: 409, bez częściowego pozytywnego wyniku. Pusta strona
formularza nie odczytuje payloadów. GET-only, staff-only, no-store.

## Co oznacza wynik

Kontrola obejmuje całą wybraną paczkę do25 stron/50MiB, także kopie i sceny
poza limitem wyświetlania lub niedopasowane do filtrów. Uszkodzenie późniejszej
kopii odrzuca całość, nawet jeśli pierwsza scena spełnia zapytanie.

Filtr przestrzenny sprawdza nakładanie prostokątnych obwiedni geometrii,
z uwzględnieniem dotknięcia granicy. Nie jest to dokładne przecięcie polygonów,
przycięcie geometrii, pełna kontrola topologii ani maska Polski/UE. Obwiednia
może dawać fałszywe dopasowania względem rzeczywistego obszaru zainteresowania.

Zwracamy wystąpienia scen, bez deduplikacji pomiędzy stronami czy paczkami.
`matched_occurrences_in_batch` liczy wszystkie dopasowania paczki,
`returned_occurrences` pokazane wyniki, `truncated` ograniczenie przez limit.
Brak dopasowań oznacza tylko brak w tej zweryfikowanej paczce, nie brak u dostawcy.
Wyższy limit uzupełnia wyniki tej samej paczki; „następna paczka” nie zastępuje
wyników obciętych limitem na bieżącej paczce.

## Eksport

GeoJSON FeatureCollection, nazwa `geoklimat-stac-scenes.geojson`, limit 5MiB,
`application/geo+json`, nosniff, no-store i ETag obliczony z faktycznej odpowiedzi.
Można otworzyć plik jako warstwę wektorową w narzędziu GIS. Nie przeprowadzono
jeszcze odbioru przez użytkownika w zewnętrznym GIS; przetestowano strukturę,
geometrie, odpowiedź HTTP i rzeczywiste eksporty.

Każdy obiekt ma geometrię źródłową oraz allowlistę właściwości:
`scene_id`, `collection`, `datetime`, `snapshot_id`, `page`,
`source_feature_sha256`. ID obiektu łączy numer kopii i ID sceny, zachowując
oddzielne wystąpienia. Hash cechy jest hashem kanonicznej źródłowej cechy,
nie hashem samej projekcji eksportowanej i nie podpisem dostawcy.

Pole `geoklimat` zawiera parametry, liczniki, czas odczytu i wynik weryfikacji,
w tym kwity kopii (ID/strona/SHA256/bajty). Nie eksportujemy assets, adresów
pobrania, tokenów, pełnej konfiguracji ani dowolnych właściwości dostawcy.
Teksty HTML są escapowane. Błędy nie ujawniają surowych wyjątków.

Globalna unikalność, aktualne pełne30dni, dokładna maska i cały epik4.5 nadal
nie są odebrane. Limit cache15GB, retencja30dni i kolektory pozostają bez zmian.

## Weryfikacja 2026-10-03T12:30Z

12 nowych testów;36 celowanych PASS5.622s i764 pełne testy PostgreSQL PASS34.995s.
Ruff lint/format380 plików, Django check i brak migracji PASS.
Budowa obrazu web PASS; pięć usług running, baza healthy, bez restartowania usług.
Testy: projekcja/kwity/ETag, daty i ID, bbox/granica/pusty wynik, uszkodzona późniejsza
kopia mimo limitu1, powtórzenia, budżet wyniku, transakcja read-only/brak HTTP,
HTML escaping, staff/GET, błędy400/409 i nawigacja z zachowaniem filtrów.

Rzeczywiste strony1–25: PL kanał4/przebieg62 oraz UE kanał6/przebieg44:
każdy eksport limit250 zawiera250 wystąpień, odpowiednio173544 i173571 bajtów.
Filtr dokładnego ID pierwszej sceny daje po1 dopasowaniu. Istniejący staff,
RequestFactory: HTML/GeoJSON200/no-store dla obu; GeoJSON nosniff.
Wynik jest stanem odczytu, a nie obietnicą niezmienności bieżącej bazy.
## Świeże próbki w katalogu GIS

Uzupełnienie2026-10-03: [STAC-RECENT](STAC-RECENT.md), kanały18/19, można
filtrować i eksportować tym samym panelem. Zakres to najwyżej20scen z24h,
nie pełny katalog dobowy ani dokładna maska; istniejące limity eksportu bez zmian.
