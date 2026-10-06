# Bank Danych o Lasach — katalog kolekcji

Pakiet4.5.4, rejestr39, 2026-10-03. Adapter opisów kolekcji gotowy;
pełne dane przestrzenne i akceptacja biznesowa pozostają otwarte.

2026-10-03T18:19Z: [trzy próbki geometrii](BDL-ACTIVATION.md) aktywne,
po1kopii; oryginalne bajty/atrybucja/CC BY4.0. Nie pełny konektor39.

2026-10-03T18:04Z: [BDL oryginalne próbki](BDL-RAW-SAMPLES.md) kod gotowy,
trzy profile22–24 disabled/unapproved/0kopii. Warunki/aktywacja nadal otwarte.

2026-10-03T17:45Z: [diagnostyka geometrii](BDL-GEOMETRY-PROBE.md) gotowa.
Trzy kolekcje przeszły jednorazowe próby po1obiekcie, bez zapisu/aktywacji.
API deklaruje CC BY4.0, trwałe kopie i zakres zasad portalu nadal do weryfikacji.

## Stan po aktywacji — 2026-10-03T17:33Z

Zastępuje wyłączony/pending stan opisany w dowodach17:01Z poniżej.
Source201 jest aktywny i zatwierdzony, harmonogram co360min. Powtórzenie
configure zachowało konfigurację. Świeży zapisany audyt200/20/0/allowed-or-404;
dry-run importu20/0; dry-run aktywacji1eligible; apply aktywował tylko ten kanał.
Pierwszy import utworzył20 wpisów; kontrolowane powtórzenie pobrało20 i zachowało
wszystkie bez zmian, bez duplikatów. Rejestr39 nadal needs_connector.

Worker bc6abebb… otrzymał wyłącznie SIGTERM, naturalny exit0
2026-10-03T17:29:04.722Z; ten sam kontener z bindem repo uruchomiono
17:30:43.590Z, restart policy unless-stopped bez zmian. Web/DB/AI nie restartowano.
Log17:33Z potwierdza dalsze zwykłe cykle danych i newsów; nie jest dowodem
automatycznego ponownego pobrania BDL przed kolejnym terminem za6godzin.

Odkryto brak dataset_catalogue w filtrze core.content_scope: dodano ten typ
do katalogów. Dotyczy też istniejących INSPIRE i funding-reference, nie zmienia
decyzji redakcyjnych ani wpisów. Nowy niezależny test pilnuje obu widoków.
Kontrola odczytowa liveDB:20items/20grup, catalogues20/publications0,
brak dopisanych dat publikacji; RequestFactory home obu filtrów i registry39 HTTP200.
Nie przeprowadzono wizualnego testu przeglądarki.

16 testów celowanych PASS0.696s; pełne846PGtests PASS51.240s,
Ruff lint/format411 PASS, checkPASS, brak migracji, buildwebworkerPASS.
5usługrunning/DBhealthy. Bez quota15GB/retencji30dni/resetów/XLS/Gitpush.

## Kontrakt

Oficjalny [wykaz usług BDL](https://www9.bdl.lasy.gov.pl/portal/uslugi-ogc?v=1)
wskazuje OGC API Features. Pobieramy tylko
`https://ogcapi.bdl.lasy.gov.pl/collections?f=json`: jeden GET katalogu,
plus kontrola robots w istniejącym transporcie; limit5MiB, 1–100 kolekcji.
Dokładny URL/profil/self, unikalne bezpieczne ID i itemType feature są wymagane.
Paginacja lub zmiana kontraktu odrzuca całą partię.

Osobne metadane dataset_catalogue: nazwa, opis, ID i hash tych pól.
Adres kolekcji musi być potwierdzony jej dokładnym linkiem self w BDL.
Pozostałe linki ignorujemy: żywy katalog zawiera canonical do Natural Earth
i przykładowych danych MapServer/GitHub; nie traktujemy ich jako licencji
ani pochodzenia BDL. Nie pobieramy geometrii, /items, załączników i formularzy.
Daty publikacji/aktualizacji pozostają puste. Zmiana opisu nie oznacza zmiany
danych; zgoda na redystrybucję danych przestrzennych niepotwierdzona.

## Konfiguracja

```powershell
docker compose exec -T web python manage.py configure_bdl_catalogue
docker compose exec -T web python manage.py configure_bdl_catalogue --apply
docker compose exec -T web python manage.py audit_sources --source registry-039-bdl-collections
```

Domyślnie dry-run, brak HTTP, wspólna blokada importu i transakcja.
Tworzy tylko wyłączone źródło, interwał360min, activation_approved=False.
Powtórzenie zachowuje zgody, audyt, historię i interwał administratora;
zmieniony profil/linkage/state powoduje odmowę. Rejestr pozostaje needs_connector.

## Dowody 2026-10-03T17:01Z

Świeży docelowy parser przez Client: HTTP200/81498B/allowed-or-404,
20 wpisów/20 różnych kluczy/0 odrzuceń, bez geometrii i dat źródłowych;
bez zapisu do bazy i bez obchodzenia robots.
Pierwsza sucha konfiguracja busy, następna PASS; apply utworzyło Source201.
Odczyt: registry39, wyłączony/niezatwierdzony, 0 wpisów, brak audytu.
Powtórzenie konfiguracji i zapis audytu zatrzymała blokada aktywnego importu:
nie potwierdzono ich na żywo, idempotencja potwierdzona jednostkowo.
Nie zatrzymywano/odświeżano workera; aktywacja pozostaje do wykonania.

7 nowych testów; 845 testów PostgreSQL PASS51.423s, Ruff check/format410 PASS,
Django system check PASS, brak zmian migracji, build web/worker PASS.
Pięć usług running, baza healthy. Build nie oznacza odświeżenia procesu workera.

## Dalej

Aktywacja metadanych i odświeżenie workera zakończone — nie powtarzać.
Następne: osobno warunki geometrii, budżet/paginacja,
wydzielenia regionalne, zasięg i jakość atrybutów. Katalog nie zamyka rejestru39/4.5.
