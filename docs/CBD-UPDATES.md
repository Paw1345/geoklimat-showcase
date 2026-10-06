# CBD — początkowe karty czterech zakładek Updates (rejestr 218)

Źródło: https://www.cbd.int/. Interwał 720 minut.
Czytamy zakładki secretariat, notification, event, recommendation
w oficjalnym bloku Updates. Zbiorcza zakładka all powiela karty i nie jest
drugim źródłem. Nie wykonujemy Load More ani GET stron szczegółowych,
powiadomień, spotkań lub PDF. Strona zawiera początkowe 8 kart każdej
zakładki; to nie pełne archiwum i nie całe portfolio CBD.

## Rodzaje, daty i tożsamość

- Secretariat: komunikaty prasowe/wystąpienia z adresem PDF; tylko metadane
  karty i odsyłacz. Nie pobieramy PDF. Dzienna data z karty nie jest
  rekonstruowana z daty zapisanej w nazwie pliku.
- Notification: identyfikator YYYY-NNN zgodny z URL i rokiem daty na karcie.
- Event: identyfikator spotkania liczbowy lub nazwany. Karta nie podaje
  daty publikacji/spotkania; daty pozostają nieznane.
- Recommendation: obejmuje Recommendation i Decision, z dokładnym
  oznaczeniem uchwały oraz spotkania. Brak dat publikacji, bez domysłów.
  Odsyłacz prowadzi do spotkania, nie do sprawdzonego dokumentu uchwały.

Klucze rekomendacji zawierają identyfikator spotkania i oznaczenie dokumentu,
łącznie z gałęziami typu A and B. Dwie rekomendacje tego samego spotkania
nie zlewają się wyłącznie przez wspólny URL. Temat tytułu lub położenie
w zakładce nie jest tożsamością. Nie wymyślamy pełnych tytułów, gdy karta
używa wielokropka. Summary/updated puste, bez zgadywania autorów, stanu
prawnego lub naborów. UI ujawnia wszystkie te ograniczenia.

## Ochrona

Dokładny endpoint/profil, UTF-8, title, h2 Updates, pojedynczy blok filtra;
po jednej rozpoznanej sekcji kart każdej zakładki. Budżet 1–100 kart/zakładkę,
łącznie do200. Wszystkie karty muszą przejść walidację zanim zwrócimy partię.
Zgodne dwa linki, nagłówek/typ/identyfikator, jawne formaty dat i HTTPS
www.cbd.int. Dopuszczone tylko doc/press|speech/YYYY/*-en.pdf,
notifications/YYYY-NNN i określone meeting IDs. Nie czytamy PDF ani pagerów.
Istniejące oddzielne sekcje przycisków bez kart nie są dodatkowymi rekordami.

configure_cbd_updates: dry-run domyślnie/noHTTP/wspólny lock+transaction.
Zmiana wyłącznie pierwotnego nieużywanego wyłączonego registry-218,
chroni PK/admin/owner/name/history/linkage/720min. Repeat zachowuje audyt/zgodę.
Nie modyfikuje workbook, schematu, cache ani kursorów. Uruchomienie dopiero
po testach/fresh audit/dry import/activation. Worker SIGTERM naturalexit0,
bez SIGKILL/resetu, następnie unless-stopped.

## Dowody

2026-10-01: GET200/parser32/0 (8 każdego rodzaju), no child/PDF GET.
Pierwsza próba wykryła event z nazwanym ID i czterema polami nagłówka;
dopuszczono ten jawny wariant z kontrolą ID/URL, nie ogólny fallback.
12focusedtestsPASS0.450s: cztery rodzaje, nieznane daty, filename date
odrębna od etykiety, cytacje/gałęzie, shared meeting URL, stabilność,
budżety/duplikaty/schemat/daty/linki, config/admin/history/linkage/noHTTP/UI.
2026-10-01T20:34Z: 12focused/556full PostgreSQL PASS19.933s,
Ruff check/format302files, Django check, no migrations, buildPASS.
Pierwszy configure dry busy/nochange; worker SIGTERM naturalexit0.
Configure dry/apply/repeat zachował PK159/linkage/admin/720min.
Fresh audit200/32/0, dry import32/0, eligible1 dry przed apply.
Import32created; drugi niezależny GET not_modified/HTTP304/0writes.
Secretariat8+notification8 published/day; event8+recommendation8 dateunknown;
wszystkie32 source_updatedNULL/fail0.
Worker resumed20:34:25Z/unless-stopped, manifest
5ef78609d571686fb0e86b787c5966a4c2844c383a080a2605056590841f3111.
5running/DBhealthy/ready+registry218HTTP200; rekomendacjaItem14990 UI ujawnia
nieznaną datę i link do spotkania.196Sources162active34disabled/cache15GB.
Pełne34luki biznesowe4.5 pozostają, nie mylić ich z liczbą disabledSources.
Fresh OECD216 unexpectedHTTP i Ramsar217 Cannotconfirmrobots pozostają
bez aktywacji, nowych wyjątków lub zmiany polityki.
