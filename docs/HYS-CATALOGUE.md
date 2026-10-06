# Have Your Say — inicjatywy środowiskowe i klimatyczne

## Zakres

Kanał `registry-171` korzysta z publicznych odczytów GET katalogu portalu
[Have Your Say](https://ec.europa.eu/info/law/better-regulation/have-your-say_en).
Obsługujemy filtry `ENV` i `CLIMA`, wszystkie strony przekazane dla tych filtrów
oraz scalanie identycznych inicjatyw występujących w obu. To nie jest cały portal,
pełne archiwum konsultacji ani treść aktów, opinii użytkowników lub załączników.
Interfejs używany przez portal nie jest gwarantowanym, wersjonowanym kontraktem API.

Zachowujemy ID, referencję, tytuły PL/EN i tytuł oryginalny, tematy, źródłowe
statusy i etapy. Wybór tytułu: PL, następnie EN, następnie tytuł źródłowy.
Brak tłumaczeń lub etapów pozostaje jawnym brakiem danych. Link rekordu prowadzi
do publicznych metadanych JSON `brpapi/groupInitiatives/<id>`, nie do pełnego aktu.

`OPEN` jest tylko statusem zwróconym przez źródło. Daty rozpoczęcia i zakończenia
opinii zachowujemy dosłownie; źródło nie podaje strefy czasowej. Nie dopisujemy UTC,
nie wyliczamy otwarcia konsultacji ani nie potwierdzamy statusu prawnego.
Daty publikacji i aktualizacji pozostają puste, ponieważ lista ich nie dostarcza.
Widok szczegółów wyjaśnia te ograniczenia.

## Kontrole i obsługa

- Wyłącznie dokładny profil JSON, oficjalny host i dwa wskazane filtry.
- Maksymalnie 10 stron po 100 rekordów na filtr i 10 MiB odpowiedzi łącznie.
- Sprawdzenie liczebności, numeracji, pierwszej/ostatniej strony, stabilnego totalu,
  ID, tematów, schematu, kodowania UTF-8, powtórzeń i sprzecznych wersji rekordów.
- Błąd którejkolwiek strony odrzuca cały przebieg; brak częściowego importu.
- Standardowe ograniczenia transportu i robots; bez obejścia przekierowań, logowania,
  akceptacji regulaminów, POST, pobierania opinii lub plików.
- Kontrole paginacji nie dowodzą transakcyjnego snapshotu: zdalny katalog może
  zmieniać się podczas odczytu, także bez zmiany liczby rekordów.

`configure_hys` jest domyślnie dry-run, bez HTTP. `--apply` pod blokadą i transakcją
zamienia wyłącznie nietknięty, wyłączony kanał strony JS rejestru 171. Zachowuje
tożsamość źródła, powiązania, tematy i interwał 60 minut. Ponowienie zachowuje
audyt, historię i zatwierdzenie. Niestandardowa konfiguracja administratora blokuje
zamianę. Aktywacja wymaga osobnego świeżego audytu i próby importu.

## Weryfikacja 2026-10-02

Pełny odczyt: 350 rekordów ENV i 211 CLIMA, 556 unikalnych po scaleniu,
0 odrzuceń, HTTP 200. Świeży audyt potwierdził ten sam zakres.
10 testów dedykowanych i 721 testów PostgreSQL przeszło (pełna regresja 27,507 s).
Ruff lint/format: poprawne, 363 pliki; obraz workera zbudowany.
Konfiguracja dry-run/apply/powtórzenie zachowała Source128 i rejestr171.
Świeży audyt i próba importu: 556/0. Kanał aktywny co godzinę.
Pierwszy import: 556 nowych, 0 aktualizacji. Niezależne powtórzenie HTTP:
556 bez zmian, 0 nowych, 0 aktualizacji. Nie użyto odpowiedzi z pamięci.
Rejestr171 i szczegóły /items/15258/: HTTP200, zakres i ostrzeżenia widoczne.
Wszystkie 556 rekordów bez dopisanej daty publikacji.

Worker otrzymał wyłącznie SIGTERM i zakończył naturalnie kodem0 o12:27:51Z;
wznowiony o12:38:14Z na obrazie
b9e8b1f7e677cde0219de3e140f2d10f1951fd6d5cb46cc62b7253794538ecf7.
Polityka unless-stopped pozostała bez zmian. Pięć usług działa, baza zdrowa,
200 źródeł/172 aktywne, limit cache15GB bez zmian. Bez migracji schematu,
resetu historii/kursorów, zmiany workbooka, danych logowania lub przenoszenia D:.
Pełny zakres rejestru 171 i odbiór całego epika 4.5 nadal wymagają oceny biznesowej.
