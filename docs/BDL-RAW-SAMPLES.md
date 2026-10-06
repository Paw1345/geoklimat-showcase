# BDL — oryginalne kopie pojedynczych próbek

2026-10-03T18:19Z: [aktywacja próbek](BDL-ACTIVATION.md) zakończona.
Profile22–24 active/approved, kopie23071–23073/po1obiekcie. Wąska podstawa:
deklarowana przez API CC BY4.0, rawbytes/attribution/license/bez zmian.
Supersedes disabled/pending stan18:04Z poniżej; nie cały zasób BDL.

Pakiet4.5.4/4.5.9, 2026-10-03T18:04Z. Mechanizm zapisu gotowy, profile22–24
utworzone **wyłączone i niezatwierdzone**, bez kopii. Nie aktywowano geometrii.
Source201katalog pozostaje aktywny. Pełny rejestr39/4.5 nadal otwarty.

## Profile i granice

- bdl-rdlp-raw-sample, bdl-nadlesnictwa-raw-sample, bdl-lesnictwa-raw-sample.
- Dokładnie offset0/limit1, jeden obiekt/stronę/kolekcję na dzień UTC.
- Trzy strony łącznie mogą zapisać najwyżej15MiB/dzień przy5MiB/stronę.
  To limit kopii cache, nie całego ruchu sieciowego z OpenAPI/robots/retry.
- Profil PL/geojson/rejestr39/interwał1440min/stała atrybucja i config są sprawdzane.
- Brak pełnej paginacji, kolejnych regionów, scen/pomiarów i zmiany danych w payloadzie.
  Jest to codzienna próbka pierwszej strony, nie postęp przez cały zbiór.

## Zapis i pochodzenie

fetch_sample współdzieli wszystkie walidacje diagnostyki bdl_probe:
świeża deklaracja licencji/identity, dokładne URL, counts, self/next,
JSON i geometria. probe nadal zwraca wyłącznie raport, nie zapisuje danych.
Kolektor zachowuje response.body bajt w bajt, bez json.dumps lub dopisywania
atrybucji do źródłowego dokumentu. SHA256/rozmiar/media_type/czas pobrania,
rok źródłowy, deklarowana licencja i atrybucja pozostają w metadanych kopii.
Data publikacji źródła pozostaje nieznana; a_year nie jest datą pobrania.
Retencja kopii wynosi30dni od pobrania, nie od roku geometrii.
Istniejąca ścieżka retained_snapshot dla nieterminowanych GeoJSON zachowuje bajty;
wizualnego eksportu z żywej kopii nie sprawdzono, bo nie utworzono kopii na żywo.

Przed GET kontrolujemy cacheSUM deklarowanych rozmiarów (także wygasłych)
z rezerwą całej strony5MiB+1GiB. Nie jest to rezerwacja miejsca na dysku ani
budżet dla innych kolektorów. Po GET transakcja/cache advisorylock726043,
blokada kanału i ponowna kontrola profilu/zgód/dziennej kopii/realnego rozmiaru.
Kopia i harmonogram zapisują się razem; brak miejsca lub cofnięta zgoda
nie zapisują kopii. Próba błędnego przebiegu nie nadpisuje zmienionego profilu
ani zgody starym obiektem kanału. Istniejąca kopia tego dnia =>daily_limit/noGET,
również przy --force; nie przedłużamy jej czasu życia.

## Konfiguracja i próby

```powershell
docker compose exec -T web python manage.py configure_bdl_samples
docker compose exec -T web python manage.py configure_bdl_samples --apply
docker compose exec -T web python manage.py collect_data --source bdl-rdlp-raw-sample
```

Konfiguracja drydefault/noHTTP/sharedbatchlock/atomic3profilesprevalidation;
apply tworzy tylko disabled/unapproved. Powtórzenie zachowuje istniejące
zgody, audyty, kopie i harmonogram; zmieniona konfiguracja jest chroniona.
Nie zmienia master/technical/importowanego XLS ani integration_state39.
collect_data domyślnie dryrun, --apply nie zatwierdza wyłączonych kanałów.
On-demand pliki nie są tą ścieżką i nie omijają dziennego limitu.

Live dryconfigure PASS/apply22–24/repeatunchangedPASS. Odczyt:
wszystkie disabled/access_approved=False/not_tested/0kopii.
Live collect_data dryrun busy — nie pobrano danych tą ścieżką;
nie resetowano blokad ani nie odświeżano workera.
8nowychtestów,17focusedPASS0.145s; pełne863PGtestsPASS51.533s,
Rufflint/format418PASS/checkPASS/nomigrations/buildwebworkerPASS.
5usługrunning/DBhealthy, cache15GB/retencja30dni bez zmiany, bez Gitpush.

## Aktywacja i dalszy zakres

[API BDL](https://ogcapi.bdl.lasy.gov.pl/?f=html) deklaruje CC BY4.0.
[Regulamin portalu](https://www9.bdl.lasy.gov.pl/portal/regulamin) opisuje
zasady źródła/czasów i pierwotnej formy dalszego udostępnienia oraz poglądowy
charakter map. Zakres dodatkowych zasad dla trwałych kopii API nadal nie jest
rozstrzygnięty. Nie akceptowano warunków za użytkownika, nie składano wniosku
i nie kontaktowano operatora. Kod nie stanowi opinii prawnej lub zgody na reuse.

Przed aktywacją: rozstrzygnąć ten zakres, wykonać świeże audyty/próby,
sprawdzić budżet, zrealizować scoped approval i odświeżenie workera.
Paginacja/wydzielenia regionalne wymagają osobnego przebiegu z limitem i
spójnością stron, nie rozszerzenia tej próbki do pełnej bazy.
