# Bieszczadzki Park Narodowy — aktualności

Przegląd 2026-10-02: aktualna oficjalna strona https://bdpn.gov.pl/
oznacza dawną domenę jako archiwalną. Dawny endpoint z rejestru
https://www.bdpn.pl/ przekierowuje robots.txt na archiwum; nie dodajemy
wyjątku robots ani nie aktywujemy automatycznie archiwum.

## Zakres

Registry144 pozostaje powiązany z tym samym Source PK106.
Nowy profil web: https://bdpn.gov.pl/aktualnosci,
`{"strict_endpoint": true, "bdpn_news": "first-page"}`.
Interwał pozostaje 360 minut. Zbieramy pierwszą stronę kart: URL,
tytuł, skrót i datę publikacji z time/datetime. Data wyświetlana musi
być zgodna z atrybutem ISO oraz poprawna kalendarzowo.
Nie przypisujemy daty aktualizacji ani roku z tytułu jako publikacji.

Bez szczegółów artykułów, mediów, załączników, wykonywania JavaScript,
dynamicznej paginacji, pełnego archiwum i front-api. Skrót pochodzi
z karty, nie jest pełnym tekstem. To aktualności, nie potwierdzenie
otwartego naboru lub obowiązującego prawa. BIP, plany ochrony oraz
konsultacje wymagają osobnego konektora; ten etap nie zamyka ich pokrycia.

## Zabezpieczenia i obsługa

Jawnie przejrzany host bdpn.gov.pl, zwykłe robots/HTTPS/public DNS
bez wyjątków. Dokładny endpoint/profil/status200/UTF8, jeden main/h1,
kontener news, 1–100 kart, unikalne lokalne URL, jednoznaczne pola.
Zmiana struktury lub wadliwa karta zatrzymuje całą partię.
Klucz oparty na URL; zmiana slug może oznaczać nowy wpis, brak
stabilnego identyfikatora wydawcy na liście. Pozycja oraz obraz nie
wpływają na hash treści.

Konfiguracja bez HTTP: `python manage.py configure_bdpn_news`
(dry-run), zapis wyłącznie z `--apply`. Aktualizacja tylko pierwotnego,
nieużywanego, wyłączonego źródła bez historii i zmian administratora.
Blokada partii i transakcja; powtórzenie zachowuje audyt i aktywację.
Następnie świeży audit_sources, ingest --dry-run oraz activate_registry
bez --apply przed rzeczywistą aktywacją. Workbook, PK, historia i
limity cache pozostają bez zmian. Worker odświeżany wyłącznie łagodnie.

## Walidacja

9 testów parsera, konfiguracji i widoku PASS. Próba GET200: 12 kart,
0 odrzuceń, publikacje 2026-08-19–2026-09-30. Ruff, Django check,
brak migracji, pełna regresja oraz budowa obrazu wykonane przed aktywacją.
Pełna regresja: 597 testów PostgreSQL PASS (19.462s), końcowy Ruff319 PASS,
Django check bez błędów, brak migracji, obraz workera zbudowany.

2026-10-02T08:08Z: konfiguracja dry/apply/repeat, świeży audyt verified
GET200 12/0, dry import12/0 i dry aktywacja1. Aktywowano wyłącznie
registry144: 12 utworzonych, niezależne ponowne pobranie 12 unchanged,
0 created/updated/failures. Wszystkie publikacje day, updated NULL.
PK106, powiązanie rejestru i interwał360 zachowane. Przykład Item15098;
widok wpisu z ostrzeżeniem zakresu, registry144 i health/ready HTTP200.
Worker po SIGTERM zakończył bieżącą partię naturalnie exit0,
wznowiony 08:08:14Z/unless-stopped, manifest352583a0…31b2632.
Pięć usług running, DB healthy; 196 źródeł / 166 aktywnych / 30 wyłączonych.
Cache15GB bez zmian. Licznik aktywnych kanałów nie oznacza pełnego
pokrycia biznesowego: BIP i pozostały zakres parku nadal otwarte.
