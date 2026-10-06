# Copernicus EMS — pierwszy katalog aktywacji PL/UE

Stan2026-10-04T15:23+02:00: kod, testy i rzeczywisty audyt gotowe. Source202
`registry-196-ems-activations` jest wyłączony, niezatwierdzony, terms_accepted=false,
0Items/0IngestionRuns. Nie zaakceptowano warunków za użytkownika. Pytanie o zgodę
na ograniczony zapis/odświeżanie co120min pozostaje UNCONFIRMED.
Rejestr196 zachowuje needs_connector; pierwsza strona nie zamyka pełnego EMS.

## Źródła rozpoznania

- [Oficjalna dokumentacja API](https://mapping.emergency.copernicus.eu/about/how-to-harvest-cems-mapping-data/),
  strona podaje ostatnią aktualizację13Nov2025, odczyt2026-10-04.
- [API Emergency Response](https://mapping.emergency.copernicus.eu/about/how-to-harvest-cems-mapping-data/emergency-response-data/):
  starszy rapidmapping host przekierowuje; nie omijamy jego polityki/redirectu.
- [Warunki CEMS](https://mapping.emergency.copernicus.eu/terms-and-conditions/),
  nagłówek Last modified:07/06/2023 zachowany jako zapis źródłowy.
  Warunki wymagają decyzji uprawnionego użytkownika i atrybucji; dane mogą zawierać
  ograniczenia/prawa osób trzecich, bez gwarancji jakości. Nie akceptujemy formularzy
  ani warunków automatycznie. To nie opinia prawna ani zgoda na pełne mapy.
- [Zasady cytowania](https://mapping.emergency.copernicus.eu/about/citation-guidelines/),
  aktualizacja20Aug2026; atrybucja dla konkretnego kodu aktywacji i źródłowego roku.
- [Lista państw UE](https://european-union.europa.eu/principles-countries-history/eu-countries_en),
  27 państw, odczyt2026-10-04. To filtr nazw, nie dokładna maska terytorium UE.

## Wąski produkt

Dokładnie jeden GET:
`https://mapping.emergency.copernicus.eu/activations/api/activations/?limit=20&offset=0`.
512KiB transport+parser, bez POST/login/cookies/childGET/produktów/rastrów/geometrii.
Provider next zawiera HTTP. Walidujemy go jako nieaktywny wskaźnik kolejnej strony
tego samego API/limit20/offset20, nie śledzimy i nie przepisujemy na HTTPS.
Przekierowanie samego API jest odrzucane przed pobraniem celu. Robots/DNS/HTTPS,
retry i wszystkie istniejące kontrole pozostają; brak wyjątku robots dla EMS.
Dodano wyłącznie oficjalnie udokumentowany host mapping.emergency.copernicus.eu.

Wszystkie0–20 rekordów pierwszej strony muszą przejść pełną walidację, również
odrzucone geograficznie: count/previous/next/UTF8/JSONdupes/nonfinite/schema/
unikalneEMSR/EMSN/country/category/phase/booleanclosed/counters/calendar-times.
Emitujemy tylko rekordy z co najmniej jednym państwem UE27, z jawnymi aliasami
CzechRepublic->Czechia/TheNetherlands->Netherlands. Zachowujemy kontekst pozostałych
państw aktywacji. Nie wnioskujemy, że centroid, zasięg albo całość zdarzenia leżą w UE.
Brak match w pierwszej stronie nie dowodzi braku aktywacji dla Polski/UE.

Item identity: kod EMS, nie tytuł/pozycja. Recordkind dataset_catalogue,
listing_scope ems_initial_activations: osobno od publikacji/newsa.
Przechowujemy wyłącznie nazwę, państwa, kategorię, fazę, closed, źródłowe czasy,
licznikiAOI/produktów i atrybucję. Summary/published/updated puste. Nie zamieniamy
activationTime na publikację i nie dopisujemy offsetu do czasu naive.
LastUpdate zostaje raw, nie source_updated_at. Nie przepisujemy czasu z prozy.
Search_snippet/past_items_search_text/centroid/downloady są ignorowane.
Zmiana istotnych metadanych aktualizuje istniejący kod; kolejność strony i zmiana
nie-UE rekordu nie zmieniają fingerprintów wybranych wpisów.

UI szczegółów wyjaśnia: mapowanie nie jest lokalnym alarmem ani dowodem, że
zagrożenie trwa lub zakończyło się. Sourceclosed jest tylko stanem dostawcy.
Nie deklarujemy pełnej doby/30dni/archiwum/pokrycia ani integralności produktów.
Wyszukiwanie obejmie dopiero rzeczywiście zapisane tytuły, nie niepobrane mapy.
Metadane kanału informacyjnego nie są nowymi kopiami w cache15GB.

## Polecenia i bramka warunków

```powershell
docker compose exec -T web python manage.py ems_probe
docker compose exec -T web python manage.py ems_probe --fetch
docker compose exec -T web python manage.py configure_ems
docker compose exec -T web python manage.py configure_ems --apply
docker compose exec -T web python manage.py audit_sources --source registry-196-ems-activations
docker compose exec -T web python manage.py ingest --source registry-196-ems-activations --dry-run --force
```

Probe default bez HTTP/zapisów; --fetch tylko ograniczona próba, bez DBwrites.
Configure default dry; --apply tworzy wyłącznie disabled/unapproved profil,
nie przyjmuje warunków, zachowuje oryginalny XLS/rejestr i administracyjne edycje.
Repeat zachowuje audit/zgodę/history. Inne istniejące powiązanie196 -> odmowa
utworzenia duplikatu. Pierwotny workbook i integration_state nie są zmieniane.

Audyt verified nie jest akceptacją warunków ani zgodą na zapis. Ogólne
activate_registry i audit_sources--enable-verified nie aktywują profilu bez
terms_accepted=true. Ingest dodatkowo odmawia przedHTTP, a poGET pod blokadą
wiersza sprawdza konfigurację oraz enabled/activation_approved. Cofnięta zgoda
nie jest przywracana; cała paczka nie zapisuje żadnych Items.

Po bezpośredniej decyzji człowieka operator może użyć:

```powershell
docker compose exec -T web python manage.py approve_ems_metadata
# Tylko po świadomej decyzji użytkownika; nie wykonano w tej realizacji:
docker compose exec -T web python manage.py approve_ems_metadata --apply --accept-terms
```

Wymagany świeży<=15min verified/HTTP200/nonempty<=20/0rejected/allowed-or-404
audyt i disabled/unapproved profil. Komenda zapisuje wyłącznie decyzję i reuse_notes,
nie enabled/activation_approved. Repeat bez zmian/HTTP. Wersja profilu wiąże
decyzję z rozpoznaniem warunków i atrybucji; zmiana wersji wymaga osobnego przeglądu.
Następnie potrzebne osobno freshdryrun/scopedactivation+import/repeat oraz
łagodne odświeżenie samego workera, aby nowy proces znał konektor i host.
Nie wykonywać tych kroków bez decyzji i świeżych kontroli. Nigdy nie bulk-enable.

## Wyniki i pozostałe prace

18 nowych testów: parser/filter/identity/schema/nextHTTPinert/bytebudget/no-child/
conditional304/empty/dupes/rawdates/repeatupdate/UI/configguards/probe/redirect/
robotsDisallow/termsdryguard/genericactivationguard/revokeafterGETrollback.
37focusedPASS0.635s;931fullPGPASS53.404s;Ruff445/check/nomigrations/buildwebworkerPASS.
Po uzupełnieniu komunikatu registry18EMS tests PASS0.338s.
Istniejące ostrzeżenie pip-root w build, bez instalacji na hoście.

Liveprobe HTTP200/allowed-or-404/13934B/1066declared/20read/14matchedEU,
provider_next_followed=false. Wynik datowany, nie stała liczba ani pełny katalog.
Configure dry/apply/repeatPASS: Source202disabled/unapproved/termsfalse.
Freshaudit14/0verified; independentdryimport14/0HTTP200; genericactivationdry0eligible.
0Items/0Runs. Actual /registry/196/HTTP200 z ostrzeżeniem warunków, /health/ready/200.
dbhealthy/web/worker running; AIoff; bez restartów/recreate/wymiany kontenerów.
Build nie oznacza, że stary proces workera załadował nowy adapter. Kanał disabled.

Równolegle readonly audit lokalnych próbek STAC: PL18/123=1strona/2ID/149987B,
EU19/124=2strony/20ID/1497842B, SHA/schema/geometry/datesPASS w tych paczkach.
To nie pełne30dni. Bounded20/21 next15:27/15:37UTC (17:27/17:37lokalnie),
nie wymuszano przyspieszenia. HistoryPL109/EU44 pozostaje retention_review.

Otwarty krok: decyzja użytkownika o warunkach, potem tylko zakres metadanych
co120min/freshvalidation/scopedactivation/gracefulworker/import/repeat.
Pełne EMS196, mapy/geometrie/archiwum/EDO/EFFIS i epik4.5 pozostają otwarte.
Bez resetu danych, zmian15GB/retencji30dni/XLSX/D:migration/Gitpush/Drive.
