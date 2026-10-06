# Audyt 20 kanałów — 2026-09-17

Aktualizacja operacyjna 2026-09-18: import wzrósł do 5484 rekordów. Kanał EUR-Lex
zwrócił HTTP 202 dla robots.txt i pozostaje w automatycznym retry; pozostałe źródła
kontynuują pracę. Tabela poniżej zachowuje wynik pierwotnego audytu, nie obiecuje
stałej dostępności zewnętrznych serwisów.

Sprawdzenie przez GET w kontenerze: robots.txt, HTTP, selektory/parser, liczba poprawnych
rekordów. Wyniki techniczne nie stanowią potwierdzenia licencji pełnych treści.
Szczegółowe czasy i URL odpowiedzi przechowuje Source.last_audit w bazie.

| Kanał | Adapter | Wynik | Rekordy w audycie | Interwał |
| --- | --- | --- | ---: | ---: |
| MKiŚ wiadomości | HTML | zweryfikowany | 10 | 60 min |
| MKiŚ komunikaty | HTML | zweryfikowany | 10 | 60 min |
| GIOŚ wiadomości | HTML | zweryfikowany | 10 | 60 min |
| GUGiK wiadomości | HTML | zweryfikowany | 10 | 60 min |
| GDOŚ | HTML | zweryfikowany | 11 | 60 min |
| NFOŚiGW | HTML | zweryfikowany | 10 | 60 min |
| Wody Polskie | HTML | zweryfikowany | 10 | 60 min |
| RDOŚ Warszawa | HTML | zweryfikowany | 10 | 60 min |
| Ministerstwo Cyfryzacji | HTML | zweryfikowany | 10 | 60 min |
| Ministerstwo Infrastruktury | HTML | zweryfikowany | 10 | 60 min |
| KE DG ENV | HTML | zweryfikowany | 10 | 60 min |
| KE DG CLIMA | HTML | zweryfikowany | 20 | 60 min |
| ELI Dziennik Ustaw | JSON, 2026 + 2025 | zweryfikowany | 3117 | 360 min |
| ELI Monitor Polski | JSON, 2026 + 2025 | zweryfikowany | 2232 | 360 min |
| GUGiK INSPIRE | obserwacja tekstu | zweryfikowany | 1 | 1440 min |
| KE Green Data | obserwacja tekstu | zweryfikowany | 1 | 1440 min |
| EUR-Lex INSPIRE | obserwacja tekstu | zweryfikowany | 1 | 360 min |
| EEA komunikaty | RSS | wyłączony: robots.txt | — | 60 min |
| EEA publikacje | RSS | wyłączony: robots.txt | — | 60 min |
| RCL | kandydat | wyłączony: robots.txt i brak adaptera projektów | — | 360 min |

17 z 20 kanałów przeszło audyt. Pierwszy import zakończył się sukcesem: 5483 rekordy, z czego
5349 to metadane prawa ELI. Liczby opisują chwilę audytu, nie stałą wielkość serwisów.
Nie utożsamiamy liczby kanałów z liczbą instytucji ani pełnym pokryciem tematu.

W ELI robots.txt zwraca 404 — brak opublikowanych reguł, API jest publicznie
udokumentowane. EEA publikuje linki RSS, ale robots.txt zabrania ścieżek rss.xml;
obecna polityka aplikacji respektuje zakaz. Nie obchodzimy go innym User-Agentem.
RCL wymaga uzgodnienia kanału dostępu i dedykowanego adaptera projektów.

Poprawiono adres GIOŚ na https://www.gov.pl/web/gios/wiadomosci. Stary /aktualnosci
przekierowywał na stronę główną gov.pl. Dodano regresję blokującą zapis wyników po
przekierowaniu poza dział konkretnej instytucji. Selektory KE oparto na zweryfikowanej
strukturze ecl-content-block, zamiast zakładać identyczne nazwy klas dla wszystkich wersji.

URL, opiekun, tematy, zakres i warunki przechowywania znajdują się w katalogu aplikacji
oraz panelu administratora. Dalszy onboarding opisano w EPIC-0.md.
