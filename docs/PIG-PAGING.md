# MIDAS — wznawialne przebiegi

Aktualizacja2026-10-01T09:17+02:00: złoża complete18147/18147/182strony,
wszystkie182kopie32930146B/spis+payloady verified07:17Z, exporteligible50MiB.
Zastępuje wcześniejsze running, nie jednoczasowa wersja atrybutów ani wieczna
certyfikacja dostępności. Pozostałe dwa MIDAS zakończone i verified06:49Z.

Aktualizacja2026-10-01T00:30+02:00: obszary6214 i tereny6038 complete,
pełnyspis/payloady zweryfikowane. Złoża nadalrunning. [DATA-COVERAGE](DATA-COVERAGE.md)
opisuje panel dostępności i scalony eksport ukończonego przebiegu staff-only/50MiB.

2026-09-30T21:40Z: shared mechanism also supports three reviewed PIGspatial
profiles with pages50/10/10; [PIG-SPATIAL](PIG-SPATIAL.md). Existing MIDAS
config, scope hashes and cursor URLs unchanged; regressions included in388tests.

Stan zweryfikowany 2026-09-30T21:23Z. Trzy nowe kanały są zatwierdzone i aktywne,
obok zachowanych kanałów próbek. Worker kontynuuje pobieranie automatycznie.

| Kanał / karta | Zapisane strony | Obiekty / spis dostawcy | Stan |
|---|---:|---:|---|
| pig-midas-0-scan / `/data/12/` | 6 | 600 / 18147 | running |
| pig-midas-1-scan / `/data/13/` | 6 | 600 / 6214 | running |
| pig-midas-2-scan / `/data/14/` | 6 | 600 / 6038 | running |

To punkt kontrolny, nie bieżący licznik ani deklaracja kompletności. Pierwsze
dwa ręczne wywołania zapisały po 4 strony / 400 obiektów; po uruchomieniu worker
dodał po 2 strony bez resetowania kursora. 18 kopii zajmowało 2471624 B payloadów.

## Spójność i wznowienie

`ingestion/pig_paging.py` pobiera pełny spis OBJECTID i kontroluje jego zgodność
z licznikiem dostawcy. Hash spisu oraz profilu jest przypięty do DataHarvest.
Każda strona zamawia dokładnie do 100 identyfikatorów: bez OFFSET i dowolnego SQL.
Walidowane są tożsamość, kolejność, kompletność odpowiedzi, geometrie WGS84 oraz
limit odpowiedzi. Spis jest ponownie sprawdzany przed zatwierdzeniem strony.

Jedno wywołanie zapisuje najwyżej dwie strony. Zapis strony i przesunięcie kursora
są atomowe. Błąd sieci, brak miejsca lub wadliwa odpowiedź pozostawiają ostatni
zatwierdzony postęp. Zmiana spisu ID zatrzymuje przebieg: nie resetujemy go ani nie
łączymy automatycznie różnych spisów. Wymaga to osobnego przeglądu operatora;
nie ma automatycznej procedury resetu. Nie usuwaj istniejących harvestów/kopii.

`complete` oznacza odczyt wszystkich ID przypiętego spisu, nie transakcyjny obraz
atrybutów i geometrii w jednej chwili. Zmiany atrybutów przy tym samym spisie nie
są wykrywane jako zmiana tożsamości. Po ukończeniu następne pobranie tworzy nowy
przebieg zgodnie z interwałem 1440 min. Oddzielne strony nie są deduplikowane
między przebiegami i wykorzystują wspólny budżet cache.

## Kopie i eksport

Retencja 30 dni dotyczy lokalnych kopii, nie wieku złóż ani decyzji. Wspólna quota
15 GB nie została zmieniona. Eksport zawiera tylko jedną stronę i jawne `partial`,
`source_count`, hash spisu, źródło, warunki oraz czasy pozyskania/wygaśnięcia kopii.
ETag odpowiada rzeczywistym bajtom eksportu. Historyczny ukończony przebieg może
pozostać w bazie po wygaśnięciu jego kopii; nie oznacza dostępności pełnego eksportu.
Obok eksportów stron jest osobny scalony plik ukończonego przebiegu dla staff,
do50MiB po pełnej walidacji. SOPO nadalnieobsługiwane; hydro/hałdy opisuje PIG-SPATIAL.md.

## Obsługa

Polecenia w istniejącym kontenerze aplikacji (`docker compose exec -T web ...`):

```text
python manage.py configure_pig_scans
python manage.py configure_pig_scans --apply
python manage.py audit_data --source pig-midas-0-scan
python manage.py audit_data --source pig-midas-0-scan --enable
python manage.py collect_data --source pig-midas-0-scan --force
python manage.py collect_data --source pig-midas-0-scan --force --apply
```

Konfiguracja domyślnie działa jako dry-run bez HTTP/zapisów. Nowe profile powstają
wyłączone i niezatwierdzone; aktywacja wymaga audytu. Istniejący zmodyfikowany profil
nie jest nadpisywany. Te trzy profile już uruchomiono — nie trzeba powtarzać setupu.

Weryfikacja: 383 testy PostgreSQL PASS, Ruff lint/format 221 plików PASS, Django
check i makemigrations --check PASS, build workera PASS. Testy obejmują wznowienie,
zmianę spisu przy równej liczbie ID, błędną stronę, quota i rollback kursora/kopii.
Live: audyt po 100 obiektów, zapis/wznowienie trzech przebiegów, karty i eksporty
HTTP200 z kontrolą ID, pochodzenia i ETag; stare próbki po jednej kopii zachowane.
Worker SIGTERM exit0 21:18:03Z, wznowienie 21:22:23Z; wszystkie 5 usług running.
Brak migracji schematu, zmian workbooka, resetu danych lub wznowienia migracji D:.

Mechanizm jest zgodny z opisem zapytań po identyfikatorach w
[oficjalnej dokumentacji ArcGIS Query](https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/).
Warunki i zakres źródła opisano w [PIG-DATA](PIG-DATA.md). Pełny epik 4.5 i odbiór
pilota pozostają otwarte.
