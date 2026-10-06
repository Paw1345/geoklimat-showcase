# EU Vocabularies180 — katalog schematów INSPIRE

2026-10-02T10:39Z: dodano osobny kanał [wartości ośmiu słowników](INSPIRE-CODE-VALUES.md),
76rekordów/62etykietyPL/active1440min/independent repeat76unchanged.
Ten schematowy profil190/370 nie został zmieniony. Pełne180 pozostaje otwarte.

Stan2026-10-01T08:51+02:00 zastępuje oczekiwanie00:44: **aktywny i zatwierdzony**,
interwał360min. Użytkownik zatwierdził wznowienie usług i odświeżenie workera.
Worker zakończył partię po SIGTERM kodem0; świeży audytHTTP200/370/0,
dry-run aktywacji1/importu370, apply/import370created, powtórzenie370unchanged
bez nowych rekordów/aktualizacji. Wznowiono tylko worker na nowym obrazie.
Wszystkie5usługrunning, DBhealthy.370Items dataset_catalogue, wszystkie bez
wymyślonych dat publikacji/aktualizacji. Następne odświeżenie12:49Z/14:49+02:00.
405pełnych testówPG PASS18.302s,17celowanychPASS2.231s,Ruff234lint/format,
brak zmian schematu i buildweb/workerPASS. Wcześniejsze busy nie były obchodzone.

Oficjalny portal informuje o przeniesieniu INSPIRE Registry od2026-07-01 i
ukończeniu migracji/zintegrowanym przeglądaniu od2026-09-16; dotychczasowe URI
pozostają identyfikatorami. Źródła pierwotne:
[rejestr INSPIRE](https://op.europa.eu/en/web/eu-vocabularies/inspire-registry),
[komunikat migracji](https://op.europa.eu/en/web/eu-vocabularies/news/-/blogs/migration-of-inspire-registry-to-eu-vocabularies-completed),
[usługa URI/SPARQL](https://op.europa.eu/en/web/webguide/uris).
Opis jest parafrazą, nie opinią prawną ani potwierdzeniem niezmienności API.

## Zakres i ograniczenia

`inspire_vocabularies.py` pobiera jedną odpowiedź SPARQL JSON z oficjalnego
`https://publications.europa.eu/webapi/rdf/sparql`, już dopuszczonego transportem.
Dokładny SKOS ConceptScheme, prefiks URI `http://inspire.ec.europa.eu/`, nazwy
i opcjonalne opisy angielskie. LIMIT1001 wykrywa przekroczenie limitu1000,
bez OFFSET/stronicowania/mieszania kolejnych odpowiedzi. Empty/budget/schema,
obce URI, duplikaty, niemonotoniczna kolejność i inny język odrzucają całą paczkę.

To370schematów w aktualnej odpowiedzi, nie wszystkie wartości list kodowych,
pojęcia, rejestry, wersje ani historia30dni. Nie pobieramy dzieci URI, aktów,
geometrii lub danych GIS. URI HTTP zachowane jako identyfikatory/reference links,
nie zamienione na fikcyjne identyfikatory HTTPS i nie pobierane przez adapter.
Tytuł/summary pochodzą od dostawcy; pełny opis, jeśli dostępny, pozostaje w
description_raw. Dat publikacji/aktualizacji nie dopisujemy z czasu pobrania.

Rekordy są `dataset_catalogue`, nie datowanymi newsami. Hash schematu/etykiet/
opisów wykrywa ich zmiany; nie dowodzi zmian wartości kodowych lub prawa.
Katalog nie jest używany do automatycznej zmiany klasyfikacji, ustawiania dat,
publikowania ocen prawnych ani dodawania nowych adresów do transportu.
Pełna pozycja180 pozostaje needs_connector; szersze EU Vocabularies/EuroVoc
i wartości INSPIRE wymagają osobno zdefiniowanego zakresu.

## Obsługa

```text
python manage.py configure_inspire_vocabularies
python manage.py configure_inspire_vocabularies --apply
python manage.py audit_sources --source registry-180-inspire-schemes
```

Setup domyślnie dry-run/noHTTP, advisorylock/transakcja, disabled/unapproved,
odmowa nadpisania administracyjnego profilu lub innego powiązania. Interwał360min.
Aktualny setup/audyt/aktywacja/import/powtórzenie i wznowienie zakończone;
nie powtarzać odświeżenia ani resetować danych na podstawie samego dokumentu.
Nowe profile lub zmiany zakresu nadal wymagają audytu i właściwego zatwierdzenia.

Live trzy próby GET: pierwsze10schematów, pełna odpowiedź370unikalnych/71095B,
następnie parser370/0/hash4b276406d92ac9595c9ea168dac7a566a421b25faf1c9e19cd8eab290dc4df05.
Audyt powtórzył370/0. Bez obejścia robots lub nowych hostów.4celowane/402pełne
testy PG PASS17.162s,Ruff233lint/format/check/nomigrations/buildworkerPASS.
Kontrakt fetch_entries zwraca4elementy; tests obejmują brak wymyślonych dat,
jednoGET, stabilnyhash, duplikaty/język/URI/limit/profile i zachowanie adminconfig.
Powyższe próby/testy są historyczne, sprzed aktywacji. Aktualne liczniki:
190Sources141active49disabled,17DataChannels; pełne4.5/pilot nadalotwarte.
