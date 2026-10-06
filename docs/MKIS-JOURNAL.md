# Dziennik Urzędowy MKiŚ — naprawa kanału rejestru 4

Weryfikacja adresów: 2026-10-01. Stary adres
`https://www.gov.pl/web/klimat/dziennik-urzedowy` kończy się na ogólnym
portalu Gov.pl, a nie w dzienniku. Nie należy go akceptować jako treści rejestru.

Oficjalna [strona archiwalna](https://dziennikurzedowy.mos.gov.pl/)
odsyła publikacje po 1 lipca 2026 do
[nowego rejestru MKiŚ](https://www.gov.pl/web/klimat/dziennik-urzedowy-ministra-klimatu-i-srodowiska).
Kod publicznej strony wskazuje GET
`https://www.gov.pl/api/data/registers/search?pageId=21328136`.
Próba przez standardowy transport z kontrolą HTTPS/DNS/robots:
HTTP200, 14 rekordów, 0 odrzuceń; daty ogłoszenia 6 lipca–28 września 2026.
Jedynie jeden rekord ma podaną datę wejścia w życie.

## Zakres i zabezpieczenia

- Pełna lista zwrócona przez ten bieżący rejestr, maksymalnie 500 rekordów;
  brak automatycznych żądań do stron aktów, załączników lub starego archiwum.
- Stabilny identyfikator publicznej strony, osobno rok i pozycja dziennika.
  Weryfikacja ministerstwa, zgodności pozycji z tytułem i linkiem, duplikatów,
  słowników oraz rzeczywistych dat kalendarzowych; błąd zatrzymuje cały import.
- Data publikacji pochodzi wyłącznie z „Data ogłoszenia”.
  Data wydania i źródłowa data wejścia w życie są osobnymi metadanymi.
  Obsługiwane jawne warianty DD.MM.RRRR i DD-MM-RRRR; brak daty nie jest
  zastępowany datą ogłoszenia. Nie wynajdujemy godzin ani dat zmian źródła.
- Wpis ma rodzaj `legal_register_metadata`. Nie jest opinią prawną ani
  potwierdzeniem aktualnego obowiązywania. PDF i podpisów nie zweryfikowano.
  Podgląd wpisu rozdziela daty i pokazuje te ograniczenia; HTML jest escapowany.
- `configure_mkis_journal` jest domyślnie dry-run, bez HTTP, z blokadą partii
  i transakcją. Zmienia tylko nietknięty, wyłączony kanał `registry-004`,
  zachowuje PK, właściciela, tematy, wydawcę, powiązanie rejestru i interwał120min.
  Chroni konfigurację administratora i historię. Unieważnia audyt/ETag starego
  adresu; powtórzenie nie zmienia nowego audytu ani zgody na aktywację.
- Oryginalny XLSX, schemat, retencja30dni i cache15GB pozostają bez zmian.

## Kolejność uruchomienia

W istniejącym projekcie Compose z nakładką AI, narzędzia wyłącznie w kontenerze:

1. `python manage.py test ingestion.test_mkis_journal --noinput`,
   pełne testy PostgreSQL, Ruff, check, makemigrations --check --dry-run,
   build obrazu worker.
2. Dry-run konfiguracji; jeżeli blokada jest zajęta, nie omijać jej.
   Łagodne SIGTERM tylko workera, naturalne zakończenie bez wymuszania.
3. `python manage.py configure_mkis_journal`, następnie `--apply` i powtórzenie.
4. `python manage.py audit_sources --source registry-004`;
   `activate_registry --source registry-004` i
   `ingest --source registry-004 --dry-run --force`.
5. Po zweryfikowaniu wyników: scoped `activate_registry --apply`,
   import rzeczywisty i powtórzenie; wznowić worker z nowego obrazu.

## Wynik wdrożenia — 2026-10-01T13:32Z

Zakończone: dry/apply/repeat konfiguracji, świeży audyt HTTP200/14/0,
próba aktywacji jednego kanału i dry import14/0. Kanał23 `registry-004`
aktywny i zatwierdzony co120min. Import14created/0updated; powtórzenie HTTP304,
bez nowych wpisów lub zmian. Zachowane PK23, powiązanie rejestru4 i XLSX.

8 testów celowanych PASS; 499 pełnych testów PostgreSQL PASS (20.836s);
Ruff lint/format PASS, Django check PASS, brak nowych migracji, build worker PASS.
Worker SIGTERM/naturalny exit0, wznowiony13:30:56Z z obrazu
`sha256:c6004b32908c67d76f0269079473b014eae2838d70dbe2a9fabc7480517a828e`,
restart `unless-stopped`. Wszystkie5usług running, PostgreSQL healthy.
Skany Copernicus wznowiły postęp bez resetu kursorów.

196Sources/157active/39disabled; cache nadal15GB. Wszystkie14wpisów mają
precyzję daty „day”, puste daty aktualizacji źródła; ogłoszenie nie jest godziną
publikacji. Podgląd przykładu `/items/14580/`, `/registry/4/` i
`/health/ready/` zwracają HTTP200. Podgląd pokazuje osobno datę wydania
i źródłową datę wejścia w życie. Archiwum sprzed1lipca, treści/podpisy PDF,
aktualny status prawny i pełne zamknięcie4.5 pozostają poza tym odbiorem.

## Pozostała blokada GIOŚ

2026-10-01: nagłówki `https://api.gios.gov.pl/robots.txt` zwracają HTTP302
do `http://www.gios.gov.pl/`. To nie delegacja do HTTPS robots.txt.
Nie dodano wyjątku, nie pobrano danych z pominięciem polityki, nie aktywowano7.
Oficjalna dokumentacja nadal wskazuje wersjonowany endpoint v1; komunikat
o wycofaniu starego API dotyczy adresów bez wersji, nie uzasadnia zmiany v1.
API GIOŚ i pełny zakres epika4.5 pozostają otwarte.
