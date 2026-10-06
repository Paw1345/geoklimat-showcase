# STAC — audyt manifestu w wielu ograniczonych paczkach

Uzupełnienie2026-10-03T13:08Z: [oś czasu i audyt UE](STAC-TIMELINE.md).
Nowe dowody2 zawierają ID/hash/datę; stare1 nadal działają, ale nie dają
pełnej osi obserwacji. Audyt UE1623 stron/65paczek odebrał przypięty manifest,
nie całe nadal rosnące wyszukiwanie ani pełne aktualne30dni.

Pakiet4.5.8/4.5.9. CLI `stac_audit` pozwala policzyć unikalne ID i powtórzenia
między wszystkimi paczkami jednego przypiętego manifestu lokalnych stron.
Nie dodaje kolektora, nie wykonuje HTTP, nie zapisuje do bazy ani nie resetuje
danych. Dowody są lokalnymi plikami JSON, nie nowymi kopiami danych w cache.

## Trzy oddzielne operacje

1. `plan`: odczyt metadanych i rzeczywistych rozmiarów, przypięcie kanału,
   przebiegu, liczby stron, ID kopii oraz skrótów metadanych. Bez czytania treści.
2. `batch`: [weryfikacja treści](STAC-INTEGRITY.md) do25 stron i mniejszego
   z50MiB/`DATA_MANUAL_MAX_BYTES`, w osobnej transakcji read-only. Podpisany
   dowód zawiera plan, granice paczki, czas i pary ID sceny/hash źródłowej cechy.
3. `combine`: sprawdzenie kompletności i podpisów wszystkich dowodów,
   ponowne porównanie aktualnych metadanych kopii z manifestem i zliczenie
   unikalnych ID, powtórzeń oraz ID z wariantami między paczkami.

Każda kontrola treści zachowuje limit50MiB; suma osobnych kontroli może być
większa. Nie wczytujemy całego przebiegu w jednym żądaniu ani nie podnosimy
budżetu eksportu/pobrania. Łączenie czyta dowody i metadane, NIE payloady.

## Bezpieczne użycie w PowerShell

Uruchamiaj z repozytorium, przy działającym Compose. Wybierz istniejący
zatwierdzony kanał i przebieg z `/data/stac/`. Przykład4/62 pochodzi z odczytu
2026-10-03, nie jest uniwersalnym numerem dla nowej instalacji.
Zmień prefiks plików dla nowego audytu — polecenie celowo odmawia nadpisania.

```powershell
docker compose exec -T web python manage.py stac_audit plan --channel 4 --harvest 62 --output /tmp/stac-audit-plan.json
if ($LASTEXITCODE -ne 0) { throw 'Plan audytu niedostępny' }

$auditStarts = docker compose exec -T web python -c "import json; print(','.join(map(str,json.load(open('/tmp/stac-audit-plan.json'))['starts'])))"
if ($LASTEXITCODE -ne 0) { throw 'Nie można odczytać planu' }
$auditProofArgs = @()
foreach ($auditStart in $auditStarts.Split(',')) {
    $auditProofPath = "/tmp/stac-audit-proof-$auditStart.json"
    docker compose exec -T web python manage.py stac_audit batch --plan /tmp/stac-audit-plan.json --start $auditStart --output $auditProofPath
    if ($LASTEXITCODE -ne 0) { throw 'Paczka odrzucona; nie łącz niepełnego audytu' }
    $auditProofArgs += @('--proof', $auditProofPath)
}
docker compose exec -T web python manage.py stac_audit combine --plan /tmp/stac-audit-plan.json @auditProofArgs --output /tmp/stac-audit-report.json
```

Bez `--output` polecenie wypisuje JSON. `--output` tworzy tylko nowy plik,
bez nadpisywania, tworzenia katalogów ani zmiany ich uprawnień. Dozwolone opcje:
`plan` wymaga channel/harvest; `batch` plan/start; `combine` plan i listę proof.
Nie mieszaj opcji z innych trybów. Początki paczek muszą pochodzić z planu,
np.1,26,51…, nie dowolnego zakresu. Brakujące lub podwójne dowody odrzucają
całe łączenie. Nie ma częściowego pozytywnego wyniku globalnej liczby scen.

Pliki `/tmp` są tymczasowe w kontenerze. Opcjonalnie skopiuj końcowy raport:

```powershell
docker compose cp web:/tmp/stac-audit-report.json ./stac-audit-report.json
```

Nie publikuj podpisanych planów/dowodów jako trwałych certyfikatów. Podpis
chroni ich integralność w tej instalacji, nie zapewnia poufności; dowody
zawierają identyfikatory scen. Nie zawierają assets, URL paginacji ani kluczy.
Nie usuwamy automatycznie raportów operatora; `/tmp` nie jest trwałym archiwum.

## Spójność, ważność i limity

PostgreSQL REPEATABLE READ READ ONLY dla każdej operacji. Plan i dowody są
podpisane istniejącym mechanizmem Django, ważne maks.1h; wygasanie kopii lub
obserwacji może odrzucić je wcześniej. Podpis nie jest podpisem dostawcy,
urzędową akceptacją ani weryfikacją licencji. Zmiana klucza aplikacji może
unieważnić dowody. Nie pobieramy ani nie wyświetlamy klucza podpisującego.

Zmiana konfiguracji, kopii, skrótu, rozmiaru, dat lub metadanych, brak kopii,
niezatwierdzony dostęp oraz niezgodny profil są odrzucane. Wzrost liczby stron
bieżącego przebiegu nie rozszerza manifestu: ostatnia paczka kończy się na
przypiętej granicy, a raport wskazuje, że manifest nie obejmuje nowych stron.
Nie dodajemy stron z innego przebiegu ani nie zmieniamy jego kursora.

Do10000 stron/metadanych,400 paczek,4MiB podpisanego planu,512KiB pojedynczego
podpisanego dowodu i32MiB łącznych tokenów dowodów. CLI ogranicza również
odczyt plików: plan5MiB, pojedynczy dokument dowodu1MiB, suma dokumentów32MiB.
Duplikaty kluczy JSON/NaN/Infinity są odrzucane. Błędy CLI są ogólne,
bez wypisywania treści wejścia, konfiguracji, URL lub kluczy.

## Jak czytać wynik

- `all_manifest_pages_verified_at_proof_times`: każda strona manifestu
  przeszła kontrolę w czasie wykonania odpowiedniej paczki.
- `first_payload_check`, `last_payload_check`: przedział tych kontroli.
- `current_metadata_matches_manifest`: metadane odpowiadają manifestowi
  w spójnym odczycie podczas łączenia.
- `payloads_rechecked_at_merge=false`: nie czytaliśmy ponownie treści przy
  łączeniu. Uszkodzenia treści bez zmiany rozmiaru/hasha/metadanych po kontroli
  nie można wykluczyć tym odczytem; nowa kontrola paczki ponownie sprawdzi SHA.
- `manifest_covers_all_read_pages_at_merge`: manifest obejmuje wszystkie
  strony odczytane przez ten przebieg do momentu łączenia.
- `scan_complete_at_merge`: osobno sprawdzony status/kursor, nie dowód
  kompletności katalogu dostawcy ani aktualnego okna30dni.

Liczniki ID/duplikatów/wariantów obejmują wyłącznie ten manifest, jedną kolekcję
i przebieg. Wariant oznacza różną kanoniczną źródłową treść cechy, nie uznaną
korektę ani różną eksportowaną geometrię. Nie odtwarzamy odfiltrowanych obserwacji.
Dokładna maska PL/UE, rastry/assets oraz pełne aktualne30dni pozostają
niezweryfikowane. Źródłowy katalog może zmieniać się podczas paginacji;
lokalny audyt nie nadaje dostawcy gwarancji transakcyjnego snapshotu.

## Weryfikacja 2026-10-03T12:44:55Z

Polska, kanał4/przebieg62:139 stron,6 paczek,103498989 sprawdzonych bajtów.
1381 wystąpień,1381 różnych ID,0 powtórzeń,0 ID z różnymi wariantami.
Sprawdzenie treści12:44:41.847Z–12:44:53.715Z; przy łączeniu metadane nadal
zgodne, manifest obejmuje wszystkie odczytane strony, przebieg complete.
Zamrożone wyszukiwanie2026-09-03T10:43:12Z–2026-10-03T10:43:12Z.
Plan SHA256 `e1fea6e3906a1e7fa4216b4bbaff7e11736ecd5ccef2b44d8ed317e29fb1f961`.
Powstało8 nowych plików dowodowych w `/tmp`, bez zapisów do bazy ani HTTP.
UE nadal ma przebieg44 running; tego zbioru nie audytowano w całości tym pakietem.

14 nowych testów;778 pełnych PostgreSQL PASS36.449s, Ruff384 lint/format,
Django check i brak migracji PASS. Testy: unikalność/warianty między paczkami,
brak/duplikat/obcy/wygasły dowód, zmienione lub brakujące kopie, konfiguracja,
retencja, wzrost przebiegu, limit50MiB, read-only/brak HTTP, brak ponownego
SHA przy łączeniu, pliki CLI i odmowa nadpisania. Nie zamknięto całego4.5.
Budowa obrazu web PASS; wszystkie5 usług running, baza healthy. Bez restartu usług.
