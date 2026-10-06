# GIOŚ — lista aktywnych stacji API v1

2026-10-01: użytkownik zatwierdził wąski wyjątek dla oficjalnego endpointu
listy stacji, na opublikowanych warunkach API.
[Dokumentacja](https://powietrze.gios.gov.pl/pjp/content/api) i
[regulamin](https://powietrze.gios.gov.pl/pjp/content/terms_of_service)
udostępniają ten interfejs i określają źródło oraz limity wykorzystania.

## Dokładny wyjątek

- Tylko GET `https://api.gios.gov.pl/pjp-api/v1/rest/station/findAll?page=0&size=500`,
  profil `gios_stations_v1` i jawna flaga transportu. Nie obejmuje pomiarów,
  sensorów, nowych API `dane.gios.gov.pl` ani dowolnej strony GIOŚ.
- Wyjątek działa tylko, gdy próba pobrania dokładnego
  `https://api.gios.gov.pl/robots.txt` kończy się HTTP302 do
  `http://www.gios.gov.pl/`. Nie pobieramy tej strony HTTP.
  Inny status/adres, HTML zamiast robots, brak sieci lub poprawne Disallow
  nie uzyskują tego wyjątku.
- Nie zapisujemy fikcyjnego Allow dla całego hosta. Poprawne robots, jeśli
  zostanie przywrócone, jest respektowane. Audyt i wpis pokazują
  `approved-gios-stations-api-exception-2026-10-01`, nie „robots allowed”.
- HTTPS/DNS/publiczny adres/budżet5MiB pozostają; API redirect jest odrzucany
  przed pobraniem docelowej strony. Bez dodatkowych nagłówków, autoryzacji,
  częściowych odpowiedzi i automatycznych ponowień API.
- Odstęp transportu31s; jeden GET na odświeżenie co60min. Błędy HTTP429/5xx,
  sieci i parsera odraczają kolejną próbę minimum1h, bez obejścia blokady.
  Ręcznych audit/force nie należy uruchamiać wielokrotnie; próby obsługowe
  powinny ponownie przetwarzać jedną świeżą odpowiedź zamiast ponawiać pobrania.

## Zakres danych i kompletność

Próba pierwszej strony: HTTP200, 288 aktywnych stacji, około89kB.
API deklaruje `totalPages=1`, ale jednocześnie podaje pozorne
`next=page1`. Nie idziemy za tym linkiem. Parser wymaga jednej pełnej strony,
1–500 stacji, zgodnych self/first/last, właściwego dostawcy i tytułu katalogu.
Więcej stron, duplikaty ID/kodów, niepoprawne współrzędne albo zmieniony schemat
zatrzymują całość; nie zapisujemy niejawnie niepełnej listy.

Zapisujemy ID, kod i nazwę stacji, współrzędne źródłowe WGS84, miejscowość,
gminę/powiat/województwo/ulicę, identyfikator miasta i hash rekordu. Źródło:
GIOŚ - EKOINFONET. To `metadata`, nie wynik pomiaru ani alert zdrowotny.
Nie pobieramy stron stacji, sensorów, wartości pomiarów lub załączników.
Katalogowa data2017 i placeholder daty modyfikacji nie stają się datami
publikacji/aktualizacji poszczególnych stacji. Podgląd jawnie pokazuje zakres
oraz podstawę wyjątku; tekst źródłowy jest escapowany.

## Konfiguracja i weryfikacja

`configure_gios_stations` — dry-run bez HTTP, następnie `--apply`.
Zmiana dotyczy tylko nietkniętego, wyłączonego `registry-007`/rejestru7:
oryginalny profil `mode=gios,page_size=100,max_pages=20`, interwał30min,
scope „Lista stacji v1; nie pomiary ani alerty zdrowotne.”.
Zachowujemy PK/właściciela/wydawcę/powiązania/tematy/XLSX; nowy interwał60min.
Konfiguracja administratora, istniejące wpisy/przebiegi lub zmieniony zakres
blokują zmianę. Powtórzenie zachowuje nowy audyt i aktywację.

12 nowych testów obejmuje zakres wyjątku, niezmienione Disallow, prywatny DNS,
API redirects bez drugiego żądania, brak retry storm, schemat/duplikaty/strony,
brak wymyślonych dat, ochronę konfiguracji, uczciwy audyt i escaping UI.
Pełna walidacja PostgreSQL:511tests PASS20.987s; lint/format/check/brak migracji
i build worker PASS.

## Wynik live — 2026-10-01T14:07Z

Zakończone: konfiguracja dry/apply/repeat, SourcePK24 zachowane.
Jedno świeże GET oficjalnego API w operacji uruchomienia: HTTP200/288/0,
polityka jawnie oznaczona jako zatwierdzony wyjątek.
Audyt, dry import i import użyły tej samej świeżej odpowiedzi w pamięci,
bez dodatkowych żądań API. Kanał aktywny/zatwierdzony co60min;
import288created/0updated. Powtórne przetworzenie tej odpowiedzi:
288unchanged/0created/0updated. To test idempotencji tej samej migawki,
nie niezależne ponowne pobranie ani potwierdzenie stabilności wielogodzinnej.

Wszystkie288wpisów mają współrzędne i nieznane daty publikacji/aktualizacji.
Nie przyjęto dat2017 z nagłówka katalogu jako dat stacji. Podgląd
`/items/14594/` pokazuje stację, źródło oraz jawny wyjątek; HTTP200 również
na `/registry/7/` i `/health/ready/`.
Worker SIGTERM/naturalny exit0, wznowiony14:06:31Z z obrazem
`sha256:ab347fd34377752afa577a5e2a2804fb073990a9e8170b46dbeaf0fa284bba05`,
restart `unless-stopped`; wszystkie5usług running/PostgreSQL healthy.
196Sources158active38disabled; cache15GB/retencja30dni/XLSX/kursory bez zmian.
Następne pobranie GIOŚ zaplanowane15:06Z, nie od razu po restarcie.

Pełne pomiary GIOŚ, sensory, alerty i inne sześć luk GIOŚ w epiku4.5
nie są zamknięte przez dostarczenie listy stacji.
