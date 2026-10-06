# EEA — komunikaty z dozwolonej listy HTML

Stan2026-10-02T12:03Z. Oficjalne punkty:
[Press releases](https://www.eea.europa.eu/en/newsroom/news) i
[wykaz RSS](https://www.eea.europa.eu/en/newsroom/rss-feeds).
Lokalne próby standardowym Client: oba stare rss.xml są zabronione przez robots;
HTML /en/newsroom/news jest dozwolony i odpowiada200. Bez wyjątku/bypass robots,
zmian hostów, wykonywania JS, pobierania artykułów/PDF/obrazów ani stron archiwum.

## Zakres dostarczony

Osobny Source200 registry-175-press-list, aktywny360min; powiązany z174
Newsroom i175 RSS feeds. Dotychczasowe Source11/12 i ich relacje/historia
pozostają bez zmian i wyłączone. Nowy kanał nie jest naprawą ani aktywacją RSS.
Status integracji i workbook nie są przepisywane.

Pierwsza renderowana lista komunikatów:25 kart. Zapisujemy tytuł, źródłowy
skrót (standardowy limit600 znaków), odnośnik i datę dzienną z karty.
updated pozostaje puste; publikacja nie jest datą zdarzenia, wejścia aktu
w życie ani potwierdzeniem aktualności całej strony.
Najpóźniejsza data z tej próby to2026-09-14, nie dzień naszego pobrania.
Daty są przechowywane według istniejącej konwencji midnightUTC, lecz precyzja
day nie oznacza źródłowej godziny ani potwierdzonej strefy publikacji.

news_notice/eea_initial_press_cards, archive_complete/legal_status_verified/
funding_open_confirmed false. Historia wpisu wyświetla ograniczenia kanału
i oryginalny tekst daty; dane źródłowe escapowane.
Nie wdrożono raportów/publikacji EEA ani pełnego archiwum. Dozwolona lista
/en/analysis/publications odpowiada200, ale nie zawiera renderowanych kart
publikacji w sprawdzonej odpowiedzi; nie traktujemy jej jako działającego katalogu.
Pełny zakres174/175 i epik4.5 pozostają otwarte.

## Kontrole i konfiguracja

eea_press: exactweb URL/config/status200/UTF8/title/main/h1/root,
1–100 kart; jeden link/tytuł/skrót/data na kartę, ścisła ścieżka i tożsamość,
poprawny kalendarz i malejąca kolejność dat, brak duplikatów. Cała lista
walidowana przed importem. Obrazy i dodatkowy link poza listą nie są pobierane.
Tożsamość ścieżki: zmiana slug może utworzyć nowy wpis; tytuł/skrót zmieniają
hash, kolejność na stronie i adres obrazka nie zmieniają treści wpisu.

configure_eea_press: dry-run domyślny/noHTTP, blokada+transakcja, ścisłe
profile starych feedów i współdzielone relacje174/175, zachowanie owner/topics/
reuse_notes. Tworzy wyłącznie osobny wyłączony/niezatwierdzony kanał; powtórzenie
zachowuje audyt i historię. Niestandardowe relacje/config są odrzucane.
Aktywacja osobno, dopiero po świeżym audycie i próbie importu.

## Potwierdzone wyniki

8 nowych testów parsera/guardów/dryapplyrepeat/UIescaping;711 pełnych testów
PostgreSQL PASS28.618s;Ruff359/check/format/brak migracji/buildworker PASS.
Wczesne problemy fixture/UI test placement/lint poprawione przed finalną regresją.
Pierwszy dry-run wykrył rzeczywiste współdzielenie źródła11 z174; guard i
fixture dopasowane do zweryfikowanego układu, bez zmian starych relacji.

Worker ONLYSIGTERM naturalexit0 12:01:03.843Z, politykaunless-stopped unchanged.
Configure dry/apply/repeat, audit25/0verified200, ingestdry25/0,
activationdry1/apply1, import25created, niezależnyrepeat25unchanged/0updatedcreated.
Wznowiony12:02:36.282Z/image
d136a3c2ad0fa04ee8d3585cedf15e8dfc2bed173d321afd65d9f49fafcdbab7.
5usług running/DBhealthy,200Source/171enabled/cache15000000000B bez zmian.
Istniejący staff registry174/175 HTTP200; /items/15233/ HTTP200 z ograniczeniami.
Bez migracji/resetów/kursorów/usuwania/cachequota/credentials/workbook/Git zmian.
