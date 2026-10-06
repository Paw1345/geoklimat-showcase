# JRC — środowiskowe metadane zbiorów — 2026-09-30T18:49Z

## Kanał uruchomiony

2026-09-30T18:49Z: użytkownik polecił kontynuować opisany restart i aktywację.
Ten wynik zastępuje wcześniejsze oczekiwanie na zgodę oraz brak importu:

- Worker zakończył partię po SIGTERM z kodem0 o18:33:43Z. Parser w przygotowanym
  obrazie był zgodny SHA256 ze źródłem przetestowanym w poprzednim pakiecie.
- Świeży audyt18:03Z nadal spełniał limit24h; dry-run aktywacji wskazał1kanał,
  ponowny dry-run pobrania potwierdził HTTP200/2058/0.
- Aktywacja dotyczyła wyłącznie registry-181-jrc-environment. Pierwszy import
  zakończony18:36:41Z:2058created,0updated,0rejected. Powtórzenie zakończone18:49:03Z:
  HTTP200,2058unchanged,0created,0updated,0rejected. Łącznie2058Items.
- Worker z aktualnym obrazem został uruchomiony18:49:21Z; wszystkie5usług running,
  PostgreSQL healthy. Importy wykonano podczas przerwy workera pod zwykłą blokadą.
- Profil zatwierdzony/aktywny,0błędów, co360min. Następne pobranie zaplanowane
  2026-10-01T00:48:34Z; to termin harmonogramu, nie gwarancja czasu zakończenia.
- Karta181, karta rekordu11524, wyszukiwarka katalogów i health/ready:HTTP200.
  Wszystkie2058rekordów mają dataset_collection; żaden nie ma dopisanej daty
  publikacji/aktualizacji. Filtr publikacji dla tego źródła zwraca0rekordów.
- 185Sources/136aktywnych/49wyłączonych. Rejestr181 nadal needs_connector;
  34luki konfiguracji pozostają, ponieważ pełne dane JRC nie zostały zintegrowane.

Kod aplikacji nie zmienił się podczas aktywacji. Weryfikację365testów/build/lint
z przygotowania uzupełniono rzeczywistymi importami i odczytami działającej aplikacji.
Nie resetowano kursorów, nie pobrano plików ani nie zmieniono15GB/30dni.

## Oficjalne źródła i zakres

- Portal: https://data.jrc.ec.europa.eu/
- Dokumentacja publicznego SPARQL: https://data.jrc.ec.europa.eu/yasgui
- Swagger: https://data.jrc.ec.europa.eu/swagger?urls.primaryName=Public+SPARQL
- Schemat API: https://data.jrc.ec.europa.eu/sparql/docs/v1/swagger.json
- Endpoint GET: https://data.jrc.ec.europa.eu/sparql
- Schemat metadanych DCAT-AP-JRC: https://ec-jrc.github.io/dcat-ap-jrc/

Portal zaleca API katalogu v2 zamiast wycofywanego v1. Publiczny SPARQL jest
osobnym udokumentowanym interfejsem: wybieramy dokładny temat
http://publications.europa.eu/resource/authority/data-theme/ENVI (Environment)
oraz angielskie tytuły i opcjonalne opisy zbiorów typu dcat:Dataset.
Nie korzystamy z POST, loginu, katalogowego API v1 ani nie zgadujemy endpointów.

Konektor jrc_catalogue.py pobiera stronami po100, do50 żądań danych na przebieg.
Waliduje pełny schemat wiązań, język, tożsamości http://data.europa.eu/89h/,
daty, rosnący porządek i unikalność także pomiędzy stronami. Nie podąża za
linkami z odpowiedzi. Błędna strona lub przekroczenie budżetu przerywa całość,
bez zapisu częściowej partii. Pełna ostatnia strona wymaga krótszej kolejnej;
osiągnięcie limitu50pełnych stron kończy się błędem, nie obietnicą kompletności.

Tytuł i opis normalizowane zgodnie z istniejącym adapterem (opis do600znaków).
Hash obejmuje również pełną źródłową treść wiązania, więc zmiana dalszego opisu
może utworzyć nową wersję. Tożsamość i URI zbioru są zachowane przy rekordzie.
Rekord dataset_collection jest obsługiwany przez istniejący filtr katalogów.

Daty dct:modified przechowujemy jako dataset_modified_raw; nie przypisujemy ich
do published_at ani source_updated_at. Nie dodajemy strefy do wartości bez strefy.
Mogą dotyczyć samego zbioru i nie potwierdzają daty modyfikacji wpisu w portalu.
Metadane mogą opisywać globalne lub historyczne zbiory. Nie jest to dokładna
maska Polski/UE, lista aktualnych pomiarów ani pełna30dniowa historia obserwacji.
Nie pobieramy plików, geometrii, rozkładów danych ani indywidualnych licencji.
Licencja konkretnego zbioru wymaga sprawdzenia przed pobieraniem jego zasobów.

SPARQL nie daje transakcyjnego obrazu katalogu między żądaniami. Porządek i duplikaty
są kontrolowane, ale zmiana katalogu podczas paginacji może wpłynąć na widoczny
zakres. Nie deklarujemy gwarantowanej kompletności wszystkich zbiorów instytucji.
Próbne zapytanie COUNT zwróciło403; nie omijano ograniczenia ani nie ponawiano
tej operacji przez POST. Zwykłe udokumentowane SELECT GET działały poprawnie.

## Profil i ochrona konfiguracji

configure_jrc domyślnie dry-run, --apply tworzy wyłączony/niezatwierdzony
registry-181-jrc-environment, co360min. Łączy profil wyłącznie z181;
zachowuje master/technical/skoroszyt i pełne needs_connector.
Powtórzenie nie nadpisuje audytu, aktywacji ani edycji administratora;
zmieniony endpoint/config lub obce powiązanie powoduje odmowę zmiany.
Konfiguracja, audyt i import respektują wspólną blokadę PostgreSQL.

## Wyniki lokalne i rzeczywisty dostęp

2026-09-30T17:59Z: dry-run na niezapisanym Source: HTTP200,2058rekordów,
21stron (20po100, ostatnia58),0odrzuceń. Bez zapisów bazy, pobierania zasobów
i wymyślonych dat. Zwykłe Client.get zachowało robots/DNS/redirect/retry/budżety;
nie dodano nowych hostów ani wyjątków polityki dostępu.

7 nowych/365 wszystkich testów PostgreSQL PASS; Ruff lint/format208plików PASS,
Django check i makemigrations --check bez zmian. Build produkcyjny
geoklimat:jrc-environment oraz check --deploy --fail-level WARNING PASS z atrapami.
Aktualny obraz workera także zbudowany, bez restartu działającego procesu.
Testy obejmują kontrakt fetch_entries, paginację i brak częściowych zapisów,
schemat/język/tożsamość/daty, limity i przekierowania, import powtórny/wersjonowanie,
ochronę administratora, brak HTTP konfiguracji i brak zamknięcia pełnej luki.

Pierwsze configure_jrc dry-run i --apply: busy, bez zmian bazy. Worker nadal
działa; nie przerywano procesu ani nie obchodzono blokady. Następnie pod wolną
blokadą dry-run i --apply utworzyły wyłączony/niezatwierdzony profil181.
Audyt zakończony18:03:03Z: verifiedHTTP200/2058/0, tylko metadane katalogu.
Odczyt bazy:0Items tego kanału,185Sources/135aktywnych/50wyłączonych,
34luki konfiguracji;181 nadal needs_connector. GET/registry/181/: HTTP200,
profil i stan nieaktywny widoczne. Nie zmieniono oryginalnego skoroszytu.
Na etapie18:03Z pytanie o restart pozostawało bez odpowiedzi; aktywacji i importu
wtedy nie wykonano. Zgoda i późniejszy wynik uruchomienia są opisane powyżej.

Wykonana procedura: activate_registry --source registry-181-jrc-environment
(dry-run, --apply), ingest --source registry-181-jrc-environment --force --dry-run,
import i powtórzenie, wznowienie workera. Przy błędzie nie kasować rekordów/kursorów.
To nie zamyka epika4.5 ani eksperckiego odbioru pilota.

## BDL GUS — wynik rozpoznania pozycji41

Ponowienie2026-10-04T19:31Z: standardowy Client._policy dla oficjalnej
listy subjects APIv1 nadal zwraca Cannot confirm robots policy.
Próba wyłącznie robots/redirect, bez żądania subjects/danych, importu,
nowego Source/kanału lub wyjątku transportu. Odczyt portalowej instrukcji
i PDF metod przez narzędzie WWW zakończył się timeoutem; nie potwierdzono
aktualnego regulaminu ani alternatywnego dopuszczonego endpointu.
Kontynuacja niezależnym lokalnym GIS nie oznacza zamknięcia pozycji41.

Oficjalne instrukcje: https://api.stat.gov.pl/home/bdlapi
i https://bdl.stat.gov.pl/api/v1/swagger/doc/swagger.json (API v1).
2026-09-30: Client.get zatrzymało próbę listy tematów na robots.
https://bdl.stat.gov.pl/robots.txt przekierowuje do https://bdl.stat.gov.pl/bdl/:
HTTP200,text/html,75133B, zamiast polityki robots. Brak potwierdzonego dostępu
automatycznego; nie pobrano tematów API, nie dodano wyjątków ani kanału BDL.
Swagger czytelny przez wyszukiwarkę nie zastępuje audytu transportu aplikacji.
Pełne dane/statystyki pozycji41 pozostają needs_connector.
