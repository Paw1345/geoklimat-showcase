# Lokalny panel kolekcji — epik4.5

## Zakres

Staff GET /collections/ udostępnia lokalne Item record_kind=dataset_collection
z aktualnie zatwierdzonych Source. Nie pobiera treści ani nie tworzy zleceń.
Nie obejmuje newsów, słowników, dataset_catalogue BDL, katalogów usług WMS
ani kopii pomiarów/plików: mają oddzielne widoki i inne kontrakty danych.

Filtry: źródło, tytuł/lokalnie zapisany identyfikator, dokładny wskaźnik
licencji, podany/brak zapisanego DOI. Wybór źródła obejmuje tylko dostępne
kolekcje zatwierdzonych kanałów. Wyłączony, ale zatwierdzony kanał może
pokazywać historię z wyraźnym statusem harmonogramu; cofnięta zgoda ukrywa go.
Nie twierdzimy, że aktywny kanał lub dawny sukces gwarantuje bieżącą kompletność.

Identyfikator pochodzi wyłącznie z zapisanych collection_id/product_id/
dataset_code/dataset_uri (w tej kolejności); pokazujemy nazwę wybranego pola.
Nie odtwarzamy ID ani DOI z URL, tytułu, hasha lub niepobranej treści.
Brak osobnego pola oznacza brak zapisanej wartości, nie jej brak u dostawcy.
Identyfikatory z różnych schematów/źródeł nie są utożsamiane; wspólne URL
mogą występować w kilku kanałach. Licznik dotyczy rekordów, nie produktów.

Licencja z license_raw lub license to surowy wskaźnik, bez konwersji do CC
lub zgody na użycie danych. Zasięg z extent_raw/extent zachowuje bbox i
przedział czasu, w tym 0–360°, przyszłe granice i null; bez konwersji/maski.
Daty publication_date_raw/updated_date_raw/dataset_modified_raw mają osobne
pola i nie są automatycznie czasem pomiaru, modelem lub ważnością prognozy.
Hash Item jest tylko odczytany, nie ponownie obliczony. Źródło ostatniego
sukcesu i czas last_seen wpisu są oddzielnymi znacznikami lokalnymi.

## Spójność i budżety

Każda strona: PostgreSQL REPEATABLE READ READ ONLY, bez HTTP/zapisów/schematu/
aktywacji/prune/workerrefresh. Dostęp ograniczony do staff; POST405,
anonymous/nonstaff302. GET nie zmienia sesji/źródeł/kursorów ani zgód.
Przejście do kolejnej strony jest nowym lokalnym snapshotem, nie gwarancją
stałości całego eksportu między stronami ani snapshotem dostawcy.

Kolejność stabilna po Item.pk,1–100rekordów/stronę (domyślnie50),page1–100000.
Nieznane/powtórzone/niepoprawne parametry lub strona poza zakresem dają400,
bez niejawnego powrotu do pierwszej strony. Maks1000źródeł.
Preflight OCTET_LENGTH(metadata::text) wybranych rekordów <=2MiB zanim
odczytamy projekcję pól. To konserwatywny koszt całych metadanych — duże
ignorowane pola też mogą odmówić stronę; nie zwiększamy limitu automatycznie.
Następnie SQL wybiera tylko jawne pola, nie całe metadata/config/owner/errors,
opisy, kontakty ani assets. Zasięg projektowany wyłącznie do bbox/interval,
max32grupy/max32KiB; nie ujawniamy zagnieżdżonych dodatkowych pól.
Niepoprawna struktura/budżet odrzuca całą stronę409, bez częściowego eksportu.

HTML/JSON/CSV <=5MiB, no-store/nosniff, HTMLescaped, JSONbezNaN,
CSVUTF8BOM i zabezpieczenie komórek zaczynających się =,+,-,@/białymi znakami.
ETag JSON/CSV to SHA256 dokładnych zwróconych bajtów, nie token dostawcy.
JSON/CSV obejmuje tylko wybraną stronę i zawiera numer/liczbę stron oraz
lokalny licznik/zakres. Puste CSV nadal ma nagłówek; JSON zachowuje null.
Eksport jest pomocniczy, nie pełną kopią katalogów ani dowodem licencji.

Nawigacja: link dla staff na stronie głównej i we wspólnym nagłówku rejestru.
Szczegóły kolekcji/historia prowadzą do istniejącego Item; link referencji
źródłowej jest do ręcznego otwarcia, nie żądaniem importera.

## Walidacja i granice

12testów obejmuje uprawnienia,escaping,whitelist,braki/fallbackID/licencji,
filtry,paging,odmowę parametrów,CSVformula,preflight/output409,
brak HTTP/zapisów, wymuszenie read-only PostgreSQL oraz stały budżet zapytań.
Pełne wyniki i próby na rzeczywistych rekordach: VERIFICATION.md.
Cache15GB/retencja30dni/kolektory/harmonogramy nie zmienione.
Panel nie zamyka ESA/full4.5, nie potwierdza jednostek, pełnej historii30dni,
licencji produktów ani pobrania geometrii/rastrów.

## Wynik — 2026-10-04T18:26Z

12celowanych testów PASS2.219s; pełne986PostgreSQL PASS57.970s;
Ruffcheck/format461, Djangocheck, brak migracji, buildwebworker/diffcheckPASS.
Testy potwierdzają odmowę zapisu w transakcji PG read-only i brak HTTP.
Początkowe błędy długości linii/nazwy routingu szablonu poprawiono przed
pełną walidacją.

Rzeczywisty lokalny odczyt:5718rekordów/9zatwierdzonych źródeł,
115stron po50. CDS144,ADS16,CDSE423,dane.gov.pl2408,Eurostat138,
Marine307,JRC2058,CLMS221,ECMWF3. To stan lokalnych rekordów, nie kompletność
dostawców/unikatowe produkty lub odebrany zakres biznesowy.
Staff resolved-view HTML200; CLMS DOIyesJSON150wyników/50na stronie200,
MarineJSON307wyników/50na stronie200; ECMWFCSV3wiersze200,
flagipartial/ETag/no-storePASS. W próbach Client.get zabroniony, liczba zleceń
niezmieniona. Actual host anonymous302login/ready200.

Nawigacja i warningESA204 działają; źródło ESA nie utworzone.
Web/db/worker bez restartu i odtwarzania; baza healthy, AI off.
Budowa obrazów nie oznacza wymiany działających kontenerów.
