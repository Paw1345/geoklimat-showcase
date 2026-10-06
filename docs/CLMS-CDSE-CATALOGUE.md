# CLMS192 — katalog opisów kolekcji w CDSE

## Zakres biznesowy

Monitorujemy wybrane metadane kolekcji CLMS udostępnionych w Copernicus Data
Space: identyfikator, tytuł, źródłowy wskaźnik licencji i jej referencję, DOI,
bbox/przedziały czasu, słowa kluczowe oraz wybrane charakterystyki
(GSD, projekcje, instrumenty, produkt i wersja przetwarzania).

Nie pobieramy scen, rastrów, map, miniatur, kontaktów, dokumentów, pełnych
opisów ani zasobów `assets`/`item_assets`. Nie otwieramy linków do DOI/licencji
w importerze. Wyświetlenie metadanych nie tworzy zlecenia pobrania pliku.
Publikacja/aktualizacja pozostają puste: początek/koniec zasięgu czasowego
kolekcji nie jest datą jej publikacji ani ostatniego pomiaru.

Zasięg katalogu obejmuje także globalne kolekcje, istotne dla PL/UE; nie jest
to pobranie pomiarów ograniczonych geometrią Polski/UE ani 30-dniowy backfill.
Null w przedziale czasu oznacza brak podanej granicy, nie „teraz”.
Formaty COG/NetCDF i różne wersje mają różne identyfikatory; liczba kolekcji
nie jest liczbą unikatowych produktów, scen ani pobranych plików.

Rejestr192 zachowuje `needs_connector`, oryginalne pola i bazowy XLSX.
Nowy scoped Source jest odrębny od istniejącego ogólnego kanału191: ten
ostatni zachowuje konfigurację, harmonogram, zgodę, powiązanie i historię.
Odnośniki kolekcji mogą występować w obu kanałach; istniejące grupowanie
publikacji rozpoznaje te same URL, nie resetujemy ani nie przenosimy wpisów.

## Źródła i polityka dostępu — sprawdzone 2026-10-04

- [Oficjalna dokumentacja CLMS API2.0](https://eea.github.io/clms-api-docs/basic.html)
  deklaruje publiczne wyszukiwanie. Faktyczny standardowy Client odmówił
  żądania `/api/@search` na land.copernicus.eu z powodu robots Disallow;
  nie wysłano żądania treści ani nie dodano wyjątku.
- [Oficjalna dokumentacja CDSE CLMS](https://documentation.dataspace.copernicus.eu/Data/ComplementaryData/CLMS.html)
  potwierdza alternatywny dostęp przez STAC i niepełną migrację produktów.
  Nie uznajemy więc wyszukiwania za pełny katalog CLMS.
- [CDSE STAC1.1.0 i wyszukiwanie q](https://documentation.dataspace.copernicus.eu/APIs/STAC.html)
  dokumentuje `/v1/collections` i wyszukiwanie tekstowe kolekcji.
- [Polityka danych CLMS](https://land.copernicus.eu/en/data-policy) opisuje
  otwarty dostęp z atrybucją, oznaczaniem modyfikacji i bez sugerowania
  oficjalnego poparcia UE. Zachowujemy referencję i atrybucję dostawcy.

W rzeczywistej odpowiedzi kolekcje zawierają wskaźniki `other` oraz
`proprietary`, mimo wspólnej referencji do polityki CLMS. Nie zamieniamy ich
na wymyśloną licencję CC ani nie wyprowadzamy zgody na pobieranie/redystrybucję
produktów. Konektor zbiera wyłącznie opisy publicznego katalogu.
Nie akceptowano warunków CEMS; EMS196, EDO200 i EFFIS202 nie są aktywowane
przez ten pakiet. To rozpoznanie techniczne, nie opinia prawna.

## Kontrakt techniczny

Profil: `registry-192-clms-cdse`, adapter `stac`, config
`{"mode":"clms_cdse_collections","strict_endpoint":true}`;
GET `https://stac.dataspace.copernicus.eu/v1/collections?q=clms&limit=100`.
Istniejący zatwierdzony host, HTTPS/public DNS/robots/bez kont i ciasteczek.
Interwał360min, tematyGIS/informacja przestrzenna/publiczne API.

Maksymalnie10 stron po100 kolekcji, 2MiB na odpowiedź, 10MiB na przebieg.
Po sukcesie lub błędzie przywracamy poprzedni limit Clienta, żeby nie zmienić
budżetu następnego źródła. Redirect takiego żądania jest odrzucany przed GET
celu, również gdy cel jest na tym samym hoście.

Sprawdzamy UTF8/JSON (bez powtórzonych kluczy/NaN), HTTP200, stabilne
numberMatched/numberReturned, dokładny zakres q/limit/offset, self/next/previous,
GET, pełne liczności stron, unikatowe rosnące ID `clms_`, typCollection,
STAC1.1.0, tożsamość self, referencję licencji, DOI, skończone bbox2D,
prawidłowe przedziały dat z offsetem i dodatnią rozdzielczość.
Rosnąca kolejność ID została zaobserwowana w bieżącym katalogu; zmiana tej
kolejności wymaga przeglądu, nie jest dowodem gwarancji sortowania dostawcy.
Identyfikator źródłowy jest zachowany w całości w metadanych i URL; `external_id`
używa standardowego `safe_entry`/SHA256 (64 znaki), nigdy obcięcia ID kolekcji.
Kolejność sprawdzamy po źródłowych ID, nie po hashach. W pierwszej rzeczywistej
próbie zapisu wykryto15 identyfikatorów dłuższych niż kolumna64: atomowe
rollbacki zachowano jako diagnostykę, źródło wstrzymano, poprawiono normalizację
i dodano regresję dla długich ID mających wspólny prefiks. Nie poszerzano
schematu ani nie usuwano historii.

Nie ufamy dostarczonemu next jako instrukcji: sprawdzamy jego zgodność
i konstruujemy własne następne żądanie z tego samego profilu. Zmieniona liczba,
duplikat, nieoczekiwany typ/licencja, niepełna strona lub przekroczony budżet
odrzuca cały przebieg, bez częściowego zapisu. Stabilny licznik i porządek
nie gwarantują transakcyjnego snapshotu dostawcy ani pełnego CLMS.
Zmiany w zachowanych metadanych tworzą zwykłą historię Item; zmiany w
ignorowanych pełnych opisach/zasobach nie tworzą pozornych rewizji wpisu.

## Przygotowanie i aktywacja

```powershell
docker compose exec -T web python manage.py configure_clms
docker compose exec -T web python manage.py configure_clms --apply
docker compose exec -T web python manage.py audit_sources --source registry-192-clms-cdse
docker compose exec -T web python manage.py ingest --source registry-192-clms-cdse --force --dry-run
docker compose exec -T web python manage.py activate_registry --source registry-192-clms-cdse
```

Configure jest dry-run domyślnie, bez HTTP; apply tworzy tylko wyłączone,
niezatwierdzone źródło i powiązanie192. Powtórzenie zachowuje audyt,
aktywację, interwał i historię. Zmieniony profil/linkage/stany rejestru
lub istniejące inne źródło nie są nadpisywane. Operacja jest atomowa pod
wspólnym ingestion lock, nie wymuszać ani nie resetować tej blokady.

Aktywacja wymaga świeżego niepustego audytu0rejected, niezależnego dry-importu
i zgody w zakresie zwykłych kanałów metadanych. `activate_registry --apply`
wykonywać wyłącznie ze wskazanym slugiem. Stary worker nie zna nowego adaptera:
przed włączeniem schedulerowi należy łagodnie odświeżyć wyłącznie worker
(SIGTERM, naturalne wyjście0, start tego samego kontenera/bindmount).
Nie restartować web/db/AI, nie wymuszać zatrzymania ani nie odtwarzać kontenera.

Importer stosuje ogólną Source-policyguard przed HTTP i w transakcji zapisu;
`--force` nigdy nie omija aktywacji. Testy używają wyłącznie mocków HTTP.
Wyniki testów, prób rzeczywistych i stan aktywacji zapisujemy w VERIFICATION.md.

## Wynik wdrożenia — 2026-10-04T16:47Z

Source203, powiązany wyłącznie z rejestrem192, jest aktywny i zatwierdzony
co360min. Świeży audyt oraz niezależny dry-import:221accepted/0rejected.
Import10531 zapisał221kolekcji;10532 zwrócił221niezmienionych/0nowych/
0aktualizacji. Wszystkie klucze są poprawnymi SHA256 pełnego źródłowego ID;
daty publikacji/aktualizacji pozostały NULL. Wcześniejsze atomowo wycofane
błędy długości ID10527/10528 zachowano; naprawę poprzedziło wstrzymanie
tego źródła, regresja i ponowne pełne sprawdzenie.

Rzeczywiste trzy strony100+100+21 miały łącznie1823005B:
170wskaźników other,51proprietary,150DOI i221zasięgów. Nie pobrano produktów.
Finalne964testy PostgreSQL PASS62.609s, Ruffcheck/format453, Djangocheck,
brak migracji, buildweb/worker i diffcheckPASS. Testy HTTP pozostają mockowane;
audyt/importy i poniższe odpowiedzi WWW były niezależnymi próbami rzeczywistymi.

Wyłącznie worker dwukrotnie otrzymał SIGTERM i naturalnie zakończył się0;
po korekcie uruchomiono ten sam kontener16:42:15.415Z, bez odtwarzania,
zmiany restart policy lub restartu web/db. Wznowione zwykłe źródła wykonały
poprawne przebiegi; baza healthy, AI wyłączone. Katalog CLMS, wpis16521
oraz health/ready odpowiedziały200. Kolejny import CLMS zaplanowany
22:42:10Z (2026-10-05T00:42:10+02:00); nie wymuszano następnego przebiegu.
Powtórzenie configure po wznowieniu workera trafiło na zajętą blokadę
i niczego nie zmieniło; wcześniejsze configure dry/apply/repeat przeszły.

Kanał CDSE191/Source141 i needs_connector192 zachowane, EMS202 nadal
wyłączony/niezatwierdzony/bez zaakceptowanych warunków i bez danych.
Nie zmieniono XLSX, cache15GB, retencji30dni ani migracjiD:. Bez push/deploy.
Ten zakres metadanych jest wdrożony; pełny rejestr192 i epik4.5 pozostają otwarte.
