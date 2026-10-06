# BDL — manifest RDLP przed przebiegiem geometrii

Aktualizacja2026-10-03T18:47Z: [BDL-SCAN.md](BDL-SCAN.md) opisuje nowy
trwały ręczny mechanizm, kanał25disabled/unapproved/0kopii. Zastępuje poniższe
„brak nowego kolektora” wyłącznie jako stan kodu/przygotowania; brak aktywacji/importu.

Stan: 2026-10-03T18:35Z. Narzędzie diagnostyczne gotowe; brak nowego kolektora,
DataHarvest, trwałego kursora, kopii geometrii lub aktywacji. Istniejące próbki
22–24 pozostają bez zmian. Pełny import BDL i epik 4.5 nadal otwarte.

## Uruchomienie

Domyślnie tylko plan, bez HTTP i zapisów do bazy:

```powershell
docker compose exec -T web python manage.py bdl_manifest
docker compose exec -T web python manage.py bdl_manifest --fetch
```

Druga komenda wykonuje dwa GET przez istniejący transport z kontrolą robots:
deklaracja OpenAPI (do 5 MiB), następnie dokładnie
`https://ogcapi.bdl.lasy.gov.pl/collections/rdlp/items?f=json&limit=100&skipGeometry=true`
(do 256 KiB). Nie podąża za linkami i nie pobiera geometrii. Nie akceptuje
regulaminów, nie wysyła formularzy i nie kontaktuje się z operatorem.

Walidacja wymaga dotychczasowej tożsamości API i deklaracji CC BY 4.0,
FeatureCollection, 1–100 obiektów, zgodnych całkowitych numberMatched/Returned,
unikalnych całkowitych id, roku danych i nazw/kodów regionów, pustej geometrii,
dokładnego self i braku next. Duplikaty kluczy JSON i wartości niefinitywne
są odrzucane wspólnym czytnikiem. Redundantne properties.id jest opcjonalne
(oficjalna odpowiedź go pomija), ale musi zgadzać się z feature.id, gdy występuje.

Posortowane pary id/rok tworzą scope_sha256 niezależne od kolejności odpowiedzi.
Oddzielny response_sha256 dotyczy oryginalnych bajtów. Manifest jest raportem
w pamięci, nie zapisem w aplikacji. Rok danych nie jest datą publikacji.

## Próby rzeczywiste i ograniczenie

GET manifestu: HTTP 200, allowed-or-404, 5358 B, 17/17 unikalnych id 1–17,
rok 2026, wszystkie geometrie null. Nie zakodowano stałej liczby 17.
OpenAPI dokumentuje skipGeometry; zweryfikowano konkretny endpoint RDLP.

Próba drugiej strony geometrii (offset=1, limit=1, limit automatyczny 5 MiB)
została zatrzymana przez transport: `Response exceeds size/time budget`.
Nie rozróżniono tutaj przyczyny rozmiar/czas i nie poznano pełnego rozmiaru strony.
Nie rozszerzono globalnego limitu i nie wykonano większego ponownego pobrania.
Wcześniejsza pierwsza próbka nie dowodzi, że pozostałe strony mieszczą się w limicie.

Komplet identyfikatorów NIE oznacza kompletu geometrii ani transakcyjnie spójnego
snapshotu dostawcy. Raport jawnie full_dataset_coverage=false i
provider_snapshot_consistency_verified=false. Dalszy etap: przypięty manifest,
jedna geometria według id, zapis oryginalnych bajtów i atomowy kursor/snapshot,
kontrola zmian manifestu, ograniczony budżet oraz osobna ścieżka na żądanie dla
dużych odpowiedzi. Nie wolno automatycznie pomijać dużego obiektu i raportować pełności.

## Weryfikacja

7 nowych testów manifestu; pełna regresja PostgreSQL: 874 testy PASS, 52.268 s.
Ruff check PASS, format check 425 plików PASS, Django check PASS,
makemigrations --check --dry-run: brak zmian; build web/worker PASS.
Istniejące ostrzeżenie pip uruchamianego jako root występuje podczas budowania,
bez instalacji pakietów na hoście. Brak restartu usług, zmian produkcyjnej bazy,
resetu kursorów, zmiany limitów/retencji i push do GitHub.
