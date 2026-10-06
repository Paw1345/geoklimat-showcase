# IMGW-PIB — oficjalny RSS wiadomości (rejestr 32)

Endpoint https://imgw.pl/feed/ wskazany na stronie https://imgw.pl/
przez link application/rss+xml „Kanał z wpisami”. Nie comments/feed.
Interwał pozostaje 60 minut. To wiadomości instytutu, nie pomiary i nie
potwierdzone ostrzeżenia meteorologiczne. Nie zastępuje kolektorów danych IMGW.

Zapisujemy tytuł, GUID/ID WordPress, link, pubDate i źródłowe kategorie.
Treść description/content:encoded przekazana w RSS jest ignorowana
i nie jest zapisywana jako streszczenie lub pełny artykuł. Nie pobieramy
stron szczegółowych, obrazów, załączników lub archiwum. Nie wykonujemy
automatycznej kwalifikacji komunikatu jako ostrzeżenia lub otwartego naboru.
Autorów nie rekonstruujemy z treści.

RSS nie podaje osobnej daty aktualizacji wpisu. Ogólny feedparser
potrafi zwrócić published jako domyślny updated, mimo że updated nie ma
w XML. Ten profil czyta jawne pola RSS i zapisuje updated jako nieznane.
lastBuildDate kanału nie jest datą aktualizacji każdej wiadomości.
pubDate wymaga daty kalendarzowej, czasu i jawnej strefy RFC822;
sprawdzamy także zgodność dnia tygodnia. Nie bierzemy dat konferencji z tytułu.

Tożsamość: dodatni ID z GUID https://imgw.pl/?p=ID z isPermaLink=false.
Zmiana tytułu/sluga to aktualizacja, nie nowy rekord; pozycja w kanale
nie tworzy zmiany. Link wpisu musi wskazywać HTTPS imgw.pl/slug/ bez query.
Cała partia wymaga unikalnych GUID i URL, 1–100 wpisów, 1–30 kategorii
na wpis i pojedynczych pól XML. Dokładny kanał IMGW-PIB/pl-PL/link/selfURL/
RSS2/UTF8; DTD/entity declarations odrzucane. Brak fallbacku HTML.

configure_imgw_news dry/noHTTP/lock+transaction, zmienia tylko nieużywany
wyłączony registry-032 z pierwotnego portal-web na feed. Chroni PK/owner/name/
history/admin/linkage/60min. Repeat zachowuje audyt i zgodę.
Bez zmian workbook/schema/cache/kursorów. Po testach: configdryapplyrepeat,
fresh audit/dryimport/dryactivation przed apply/import/repeat.
Worker tylko SIGTERM/naturalexit0, potem unless-stopped; bez resetu/SIGKILL.

## Dowody

2026-10-02: officialGET200 RSS2/pl-PL/parser10/0, GUID dodatnie/publication aware,
updated puste.11focusedPASS0.147s: identity/title-slug/order, buildDate i
content nie udają aktualizacji, malformed XML/DTD/UTF8, schema/duplicates/
budżet/kategorie/date-weekday-timezone, config/admin/history/linkage/noHTTP/UI.
2026-10-02T07:22Z: 11focused/588full PostgreSQL PASS19.542s,
Ruff check/format314files/Django check/no migrations/buildPASS.
Configdrybusy/nochange, workerSIGTERM czekał na bieżącą partię, naturalexit0
potwierdzone inspect oraz dockerwait (bez force/resetu).
Configdryapplyrepeat zachował PK36/admin/linkage/60min.
Fresh audit200/10/0, dry import10/0, eligible1dry przed apply.
Import10created; niezależny drugi GET not_modified/HTTP304/0writes.
Wszystkie10 publishednonnull/source_updatedNULL, failures0.
Workerresumed07:21:55Z/unless-stopped, manifest
e80da93db6228c513aa4a83769a1329102349f5868379f0ba1efbb113be2b8d9.
5running/DBhealthy/ready+registry32HTTP200/Item15041UIexplicitlimits.
196Sources165active31disabled/cache15GB/full34businessgaps unchanged.
Świeże odczytowe próby: ELI23 portal dokumentacyjny200 bez kanału aktualności;
BDPN144 cross-host redirect wymaga przeglądu, nie aktywowano/nieobeszliśmy.
Bez zmian istniejących pomiarów IMGW, limitów cache, schematu lub workbook.
