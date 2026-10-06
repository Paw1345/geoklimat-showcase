# WFOŚiGW Zielona Góra i Łódź — ograniczone aktualności

Stan potwierdzony: 2026-10-01T14:25+02:00. Pakiet 4.5.2 zakończony,
nie oznacza pełnego odbioru epika 4.5 ani pokrycia wszystkich naborów.

| Rejestr | Oficjalna lista | Import | Data źródłowa | Zakres |
| --- | --- | ---: | --- | --- |
| 101 / Source82 | https://wfosigw.zgora.pl/aktualnosci/ | 20 | czas ze strefą | 20 najnowszych wpisów z osadzonego archiwum |
| 102 / Source83 | https://www.wfosigw.lodz.pl/aktualnosci/ | 16 | dzień | pierwsza strona aktualności/komunikatów |

Oba kanały są aktywne co 120 minut. Zachowano identyfikatory źródeł,
powiązania z rejestrem, nazwy, priorytety i tematy. Rejestr pozostaje
`configured`, co nie jest potwierdzeniem pełnego odbioru biznesowego.

## Kontrakt pobierania

`ingestion/wfos_news.py` wymaga dokładnej konfiguracji, endpointu HTTP200,
UTF-8, tytułu instytucjonalnej strony, jednego właściwego kontenera oraz
nagłówka sekcji. Zmiana struktury nie uruchamia ogólnego parsera zastępczego.
Daty publikacji nie są odgadywane z tytułów ani z daty importu.

Zielona Góra: oficjalna strona ma 287 wpisów w jednym dokumencie HTML.
Parser sprawdza cały ograniczony zbiór (maksymalnie 1000 rekordów), jego
unikalne ID WordPress, status publikacji, host, ścieżki, kategorie i malejącą
kolejność dat; dopiero potem zwraca 20 najnowszych. Zachowuje źródłowy
timestamp ze strefą i polski tekst daty, wymagając ich zgodności. Zakres
nie jest nazywany pełnym archiwum. W aktualnym imporcie daty sięgają od
1 października do 3 czerwca 2026. Nie otwiera dalszych stron ani artykułów.

Łódź: maksymalnie 50 kart z pierwszej strony. Nagłówek i jego opis ARIA
muszą zgadzać się po normalizacji odstępów (także NBSP), oba linki karty
muszą być identyczne. Data DD/MM/YY jest jawnie interpretowana jako rok
2000–2099 i zachowuje precyzję dzienną, nie udawaną godzinę publikacji.
Aktualny import: 24 września–16 lipca 2026. Kategorie ścieżek zostały
zweryfikowane jako `ogolne` i `konkursy-nagroda`; inne wymagają przeglądu.
Kluczem wpisu Łodzi jest adres artykułu, więc jego zmiana może tworzyć nowy
rekord; Zielona Góra ma stabilne źródłowe ID niezależne od slugu artykułu.

Oba profile importują tylko tytuł, datę, link i techniczne metadane.
Nie pobierają treści artykułów, autorów, kontaktów, obrazków ani załączników.
`news_notice`, `funding_call_verified=False` i `full_document_downloaded=False`
wykluczają traktowanie samych komunikatów jako potwierdzonych otwartych naborów.
Źródłowe daty aktualizacji pozostają puste, jeśli nie zostały podane.
Transport nadal ogranicza hosty, robots, DNS, rozmiary i przekierowania;
canonicalny host Zielonej Góry dodano jawnie, nie przez obejście przekierowań.
Cache pozostaje 15 GB, retencja 30 dni, duże pliki na żądanie.

## Konfiguracja i uruchomienie

`configure_wfos_news` domyślnie tylko opisuje plan, bez HTTP i zapisów.
`--apply` zastępuje wyłącznie nietknięte, wyłączone kolektory dawnych stron
głównych: bez rekordów/historii sukcesu/błędów, konfiguracja pusta,
dotychczasowy zakres i interwał 120 minut. Cały pakiet jest sprawdzany przed
pierwszym zapisem, pod blokadą ingestion i transakcją. Ustawienia i historia
administratora nie są nadpisywane. Powtórzenie zachowuje audyt i zatwierdzenie.
Podmiana endpointu unieważnia wyłącznie poprzedni audyt i walidatory HTTP.

Zweryfikowana sekwencja 2026-10-01:

1. Próby GET: 20+16 rekordów, zero odrzuceń; konfiguracja dry-run bez zmian.
2. Tylko worker: chwilowe wyłączenie auto-restartu, SIGTERM, naturalny exit0.
3. Configure dry/apply, świeże audyty `verified` 20+16/0, ponowna konfiguracja
   bez zmian audytów, dry activation 2 kanały, dry import 20+16/0.
4. Aktywacja obu co 120 minut, import 36 nowych rekordów/0 aktualizacji.
5. Powtórzenie: Zielona Góra HTTP304, Łódź 16 niezmienionych, zero duplikatów.
6. Worker wznowiony 12:24:54Z z obrazem
   `a3ad108ffc4c716737e637f3c9eda7fe2af950c9a1d3ae2c069f0c2539cb9fa5`,
   polityka `unless-stopped` przywrócona. Bez resetu bazy/cache/kursorów.

## Weryfikacja

8 nowych testów parserów/konfiguratora; pełne 479 testów PostgreSQL PASS
w 21.599 s. Ruff lint PASS, format273 PASS, Django check PASS,
makemigrations --check --dry-run: brak zmian, build worker PASS.
Wczesny błąd fixture bez URL i z błędną nazwą precyzji poprawiono przed
pełną regresją; żywa próba doprecyzowała zagnieżdżenie sekcji i NBSP.

Stan live: 195 źródeł, 154 aktywne, 41 wyłączonych; oba nowe kanały bez
błędów. 36 rekordów z datami źródłowymi, pustymi opisami/autorami i bez
wymyślonych dat aktualizacji. Pięć usług działa, PostgreSQL healthy,
`/health/ready/`, `/registry/101/`, `/registry/102/` zwracają HTTP200.
Pełne 34 luki rejestru pozostają osobną kolejką, nie sumować ich z liczbą
wyłączonych kanałów. Następny pakiet: wyłączone WFOŚ109/110/111 lub problem
aktywnego EUR-Lex168, po indywidualnym sprawdzeniu.
