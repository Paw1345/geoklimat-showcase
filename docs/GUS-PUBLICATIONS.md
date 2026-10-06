# GUS — katalog Środowisko. Energia (rejestr 42)

## Zakres i ograniczenia

Oficjalny endpoint: https://stat.gov.pl/obszary-tematyczne/srodowisko-energia/.
Portal jawnie oznacza się jako dotychczasowa wersja i wskazuje
https://new.stat.gov.pl/. Pokrycie nowego portalu pozostaje niezweryfikowane.
Kolektor obejmuje wszystkie pozycje obu tabel na stronie, bez pobierania
stron archiwum, szczegółów dokumentów, PDF-ów ani danych liczbowych.
Nie zamyka integracji rzeczywistych statystyk GUS/BDL (rejestr 41).

Data publikacji pochodzi wyłącznie z kolumny „Data”, z precyzją dnia.
Rok lub okres w tytule nie jest datą publikacji. Nie wymyślamy autorów,
dat aktualizacji ani streszczeń. Rodzaj dokumentu może być niepodany.
Widok wpisu pokazuje ograniczenia i odsyłacz do nowego portalu.

## Walidacja i tożsamość

Parser wymaga zgodności endpointu/profilu, UTF-8, tytułu strony, banera,
kontenera i dokładnie dwóch tabel z właściwymi nagłówkami.
Cała odpowiedź jest sprawdzana przed zwróceniem partii: 1–200 rekordów
na tabelę, pięć komórek, kolejność, poprawne daty kalendarzowe, unikalne
identyfikatory i adresy, jeden link dokumentu i opcjonalny link archiwum.
Link archiwum nie powoduje dodatkowego pobrania.

Dokumenty mają HTTPS i host stat.gov.pl. Oprócz działów Środowisko/Energia
dopuszczono cztery konkretne oficjalne przestrzenie dokumentów powiązanych
z tabelą Środowisko: gospodarka społeczna, inne opracowania zbiorcze,
statystyki eksperymentalne oraz infografiki. Nie są to nowe kolektory.
Klucz zawiera przestrzeń dokumentu, numer serii i numer wydania:
sam numer serii/wydania nie jest unikalny między działami.
Zmiana tytułu/slug jest aktualizacją, a przesunięcie pozycji w tabeli
nie jest zmianą treści.

## Bezpieczna konfiguracja i uruchomienie

Polecenie configure_gus_publications domyślnie jest dry-run, bez HTTP.
Zmienia wyłącznie nieużywane, wyłączone registry-042 z pierwotnym profilem.
Chroni historię, konfigurację administratora, powiązanie rejestru,
PK, właściciela, nazwę i interwał 360 minut. Powtórzenie zachowuje audyt
i zgodę. Nie zmienia XLSX, schematu, limitu cache ani kursorów.

Po testach: configure (dry/apply/repeat), audit_sources --source registry-042,
ingest --source registry-042 --dry-run --force, activate_registry (dry/apply),
ingest (real/repeat). Każdy wynik wymaga sprawdzenia, nie tylko kodu wyjścia.
Odświeżenie wyłącznie workera przez SIGTERM z naturalnym zakończeniem;
bez SIGKILL/resetu, potem przywrócenie unless-stopped.

## Dowody

2026-10-01T18:15Z: kontrola DB potwierdza późniejszy niezależny przebieg
harmonogramowy z 18:07:12Z: success, fetched36/created0/updated0/unchanged36,
failures=0. Supersedes otwartą kontrolę powtórzenia z 17:53Z poniżej.
Nie wykonano kolejnego wymuszonego GET ani konfiguracji/aktywacji.

2026-10-01T17:50Z: pierwszy audyt zatrzymał import, bo pięć prawidłowych
odsyłaczy należało do innych działów GUS. Nie aktywowano źródła.
Po dodaniu konkretnych ścieżek i testu kolizji przestrzeni: 11 testów PASS,
świeży audyt HTTP200/36/0 i dry-run 36/0, jedna uprawniona aktywacja.
2026-10-01T17:53Z: końcowe 522 testy PostgreSQL PASS (19.126s), Ruff check/
format (290 plików), Django check, brak migracji, budowa obrazu PASS.
Source PK40/registry-042 aktywny i zatwierdzony, interwał 360 minut.
Import HTTP200: 36 fetched/36 created/0 updated/0 unchanged.
Niezależne ponowne pobranie zakończyło się timeoutem sieciowym:
0 fetched/created/updated/unchanged, failures=1. To NIE jest udany test
powtórzenia HTTP304 ani dowód deduplikacji na żywym powtórnym imporcie.
Nie wymuszono kolejnych prób; kontrola następnego harmonogramowego importu
pozostaje otwarta. Testy lokalne chronią stabilność i rozdzielenie kluczy.

Worker SIGTERM zakończył się naturalnie kodem 0, wznowiony
2026-10-01T17:52:51Z, unless-stopped; manifest obrazu c01e923f6dcb307e3dfc0651132430eb89782f3681b14c29b79c51713a8cd3e1.
Pięć usług działa, PostgreSQL healthy, logi workera potwierdzają kolejne partie.
Ready, rejestr42 oraz wpis14886 zwracają HTTP200; UI pokazuje ograniczenia.
196 źródeł: 159 aktywnych/37 wyłączonych; cache 15 000 000 000 B bez zmian.
Pełne 34 luki biznesowe epika 4.5 pozostają otwarte.
