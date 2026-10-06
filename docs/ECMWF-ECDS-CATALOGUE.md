# ECMWF203 — publiczny katalog ECDS

## Zakres i źródła — rozpoznanie 2026-10-04

[Oficjalna strona datasetów ECMWF](https://www.ecmwf.int/en/forecasts/datasets)
wskazuje ECMWF Data Store (ECDS).
[Instrukcja ECDS](https://ecds.ecmwf.int/how-to-api) opisuje dostęp do metadanych,
API katalogu i osobne uwierzytelnione pobrania. Pobranie danych wymaga
ręcznej akceptacji warunków konkretnego zbioru; GeoKlimat tego nie wykonał.
Nie instalujemy cdsapi/nowych bibliotek ani nie tworzymy konta/tokena.

Publiczny GET katalogu odpowiedział200,9399B, allowed-or-404:
numberMatched=numberReturned=3. Obecne ID: tigge-forecasts, s2s-forecasts,
s2s-reforecasts. To trzy kolekcje ECDS, nie cała oferta ECMWF, liczba scen,
pomiarów ani dostępnych plików. Monitorujemy tytuły, DOI, słowa kluczowe,
wskaźnik licencji, zasięg i daty katalogowe oraz podany disabled_reason.
Nie wyciągamy dat/zmiennych/wersji modelu z tytułu.

Daty published/updated są przekazywane przez dostawcę i dotyczą katalogu;
nie są czasem modelu, ważności prognozy ani pomiaru. Przedział temporal
zachowuje także przyszłą granicę; nie potwierdza ona dostępnego pliku.
Zasięg przestrzenny [0,-90,360,90] zachowujemy w oryginalnej konwencji,
bez konwersji, wycinania Polski/UE lub przypisywania zweryfikowanego CRS.
Null granicy czasu nie oznacza „teraz”. Licencja other nie jest CC BY
ani zgodą na pobranie/redystrybucję produktów. To rozpoznanie techniczne,
nie opinia prawna.

Nie pobieramy opisów, kontaktów, assetów, items, DOI, GRIB/NetCDF,
archiwum/MARS, dokumentów ani child links. Nie wysyłamy POST/retrieval.
Sygnał cads:sanity_check jest celowo pomijany: zmienny timestamp kontroli
nie jest aktualizacją treści kolekcji ani gwarancją dostępności danych.

## Kontrakt i bezpieczeństwo

Konektor ingestion/ecmwf_catalogue.py, Source slug registry-203-ecmwf-ecds,
adapter stac, config mode=ecmwf_ecds_collections/strict_endpoint=true.
Dokładny URL: https://ecds.ecmwf.int/api/catalogue/v1/collections.
Nowy host ecds.ecmwf.int ma udokumentowane oficjalne pochodzenie i został
sprawdzony standardowym Client z DNS/HTTPS/robots. Bez wyjątków polityki.
Nie mylić Registry203 ECMWF z Source203 CLMS.

Jeden GET, maksymalnie2MiB/100kolekcji, cały niepusty katalog przed zapisem.
HTTP200, dokładny finalURL, UTF8JSON bez duplikatów kluczy/NaN,
numberMatched=numberReturned=liczba wierszy i unikatowe ASCII ID.
Weryfikujemy STAC1.1.0/Collection, self każdej kolekcji i całej odpowiedzi,
licencję other, DOI, skończone/ordered bbox2D (-180..360/maxwidth360),
przedziały czasu i daty z jawną strefą. Nie normalizujemy geometrii.
Next/previous lub większy/niepełny katalog odrzuca partię, bez paginowania
i częściowego importu. Redirect jest odrzucany przed GET celu.
Kolejność nie identyfikuje danych; nie zakładamy snapshotu dostawcy.

Pełny ID zachowany w metadanych i URL, external_id standardowy SHA25664.
Wybrane metadane uczestniczą w wersjonowaniu; ignorowane opisy/sanity/
kolejność nie tworzą pozornych zmian. Brak kolekcji nie usuwa historii;
nie twierdzimy, że wycofanie kolekcji ma automatycznie wykryty stan prawny.
Limit transportu przywracany po sukcesie/błędzie dla kolejnego źródła.
Obowiązuje ogólny Source-policyguard przed HTTP i podczas atomowego zapisu.

## Przygotowanie i eksploatacja

```powershell
docker compose exec -T web python manage.py configure_ecmwf
docker compose exec -T web python manage.py configure_ecmwf --apply
docker compose exec -T web python manage.py audit_sources --source registry-203-ecmwf-ecds
docker compose exec -T web python manage.py ingest --source registry-203-ecmwf-ecds --force --dry-run
docker compose exec -T web python manage.py activate_registry --source registry-203-ecmwf-ecds
```

Configure domyślnie dry-run/noHTTP, apply przygotowuje wyłączony/
niezatwierdzony Source co360min i powiązanie203. Atomic/batch lock,
bez nadpisywania administratora, aktywacji, interwału, audytu lub historii
przy powtórzeniu; odmowa przy innym profilu/linkage/istniejącym kanale.
Nie zmienia needs_connector203 ani oryginalnego XLSX/CDS193/ADS194/CLMS192.

Przed scoped activate_registry --apply wymagane testy/build, świeży niepusty
audyt0rejected oraz niezależny dry-import. Tylko worker: SIGTERM, naturalne
wyjście0 i start tego samego kontenera z repo bind; bez SIGKILL/resetu
blokady, odtwarzania lub restartu web/db/AI. Nowy profil aktywować gdy stary
worker nie działa, następnie sprawdzić import/powtórzenie i wznowić proces.
Stan i rzeczywiste dowody wdrożenia: VERIFICATION.md.

Ten zakres nie zamyka pełnego203/epika4.5, wymaga osobnego rozstrzygnięcia
pełnych danych/prognoz PL/UE, historii, jednostek i warunków produktów.
Cache15GB, retencja30dni i duże pliki na żądanie bez zmian.

## Wynik wdrożenia — 2026-10-04T18:09Z

Source204 powiązany z Registry203 aktywny/zatwierdzony co360min.
Configure dry/apply/repeat oraz powtórzenie po aktywacji zachowały stan/audyt/
harmonogram/historię. Świeży audyt i niezależny dry-import3accepted/0rejected.
Run10668 zapisał3kolekcje;10669 zwrócił3unchanged/0created/0updated.
Wszystkie external_id są SHA256 pełnego źródłowego ID.

10nowych testów, pełne974testy PostgreSQL PASS60.252s; Ruff457, check,
brak migracji, buildwebworker i diffcheckPASS. Testy HTTP mockowane, rzeczywiste
GET/audyt/import/powtórzenie są oddzielnymi dowodami.
Poprawiono początkowe błędy fixture (wymagane pola rejestru, izolacja CONFIG);
pełna walidacja przeszła po poprawkach.

ONLYworker SIGTERM naturalexit0 18:06:40.560Z, start tego samego kontenera
18:07:58.051Z/bind/unless-stopped/RestartCount0. Baza healthy, web/worker running,
AI nadal off; wznowione zwykłe importy bez nowych rekordów/aktualizacji.
Rzeczywiste WWW: katalog, Item16555 i ready200; staff widok rejestru203 render200.
Następny fetch2026-10-05T00:07:56Z (02:07:56+02:00), nie wymuszano kolejnego.

CDS164/ADS165/CLMS203 zachowały aktywację i interwał360min; EMS202 nadal
disabled/unapproved/termsfalse/0Items0Runs. Full203/4.5 nie zamknięto,
nie pobrano prognoz, nie zaakceptowano warunków, bez zmian quota/retencji/XLS/
schematu/resetu/migracjiD:/push/deploy/Drive.
