# UNEP — cztery sekcje wiadomości (rejestr 208)

Oficjalna strona: https://www.unep.org/news-and-stories.
Zakres obejmuje wszystkie karty HTML w czterech rozpoznanych sekcjach:
featured, second news list, white area i additional white area.
Nie jest to pełne archiwum ani kolektor stron filtrów. Bez dodatkowych
GET do artykułów, filmów, obrazów, CDN lub załączników.
Harmonogram pozostaje 360 minut.

Typy są źródłowe: Story, Speech, Press release, Video, Statements.
Typ musi odpowiadać segmentowi URL. Tytuł, data i typ pochodzą wyłącznie
z karty; summary/updated puste, bez wymyślonych autorów. news_notice
i funding_call_confirmed=False: komunikat prasowy nie potwierdza naboru.

Daty D lub DD Mon YYYY są parsowane jawnie z angielską mapą miesięcy.
published ma precyzję dnia, zgodną z widoczną datą. Opcjonalny timestamp
time.datetime zachowujemy jako osobną metadaną, sprawdzając kalendarz,
strefę i zgodność lokalnego dnia z etykietą. Nie przesuwamy daty widocznej
na karcie do innego dnia UTC ani nie traktujemy jej jako wejścia prawa w życie.
Literalne znaki backslash-n w oficjalnym szablonie są normalizowane
wyłącznie w etykiecie daty/typu; nie w tytule, URL lub konfiguracji.

Parser wymaga endpointu/profilu, UTF-8, tytułu strony, h1 i jednego
kontenera press_room. Każda z czterech sekcji musi mieć 1–100 kart,
łącznie najwyżej 200. Cała partia jest walidowana przed importem:
pojedynczy tekst/link/meta, znany typ, data kalendarzowa, brak konfliktów
timestampu, angielski hreflang, względny dokładny URL wiadomości i unikalność.
Menu, ilustracje i inne odsyłacze nie są rekordami.
Klucz to ścieżka typu i sluga; tytuł i położenie w sekcji nie są tożsamością.
Przesunięcie identycznej karty między sekcjami nie tworzy wersji.

configure_unep_news jest dry-run domyślnie i nie używa HTTP.
Wspólna blokada i transakcja, tylko nieużywane wyłączone registry-208;
chroni historię, ustawienia administratora, link rejestru, PK/nazwę/właściciela
i interwał. Powtórzenie zachowuje audyt i zgodę. Workbook/cache/schema/kursory
nie zmieniają się. Aktywacja po pełnych testach, fresh audit i dry import.
Odświeżenie tylko workera przez SIGTERM/naturalne exit0, bez resetu/SIGKILL.

## Dowody

2026-10-01: oficjalny GET200/parser15/0, w sekcjach 2+2+5+6.
12 testów parsera/konfiguracji/UI PASS: typy, daty/timestampy/strefa,
template whitespace, stabilna tożsamość/przesunięcie, schemat, duplikaty,
budżet grup/całości, linki, profil, admin/history/linkage/noHTTP i escaping.
2026-10-01T20:20Z: 12focused/544full PostgreSQL PASS19.894s,
Ruff check/format298files, Django check, no migrations i buildPASS.
Pierwszy configure dry był busy/nochange; worker SIGTERM naturalexit0,
configure dry/apply/repeat zachował PK149/linkage/admin/360min.
Fresh audit200/15/0, dry import15/0, eligible1 dry przed apply.
Import15created; niezależny drugi HTTP200:15unchanged/0created/0updated.
15rekordów day precision/source_updatedNULL, failures0.
Worker resumed20:20:15Z/unless-stopped, manifest
a024ca3325858c644cbd37d0111e69da34b7de73a6247b9eb163425048e88c61.
5running/DBhealthy, ready/rejestr208 HTTP200, Item14951 przedstawia zakres.
196Sources161active35disabled/cache15GB bez zmian; pełne34luki4.5open.
Fresh readonly UNFCCC209 HTTP200 bez tytułu/h1/artykułów: nierozpoznana
odpowiedź, nie potwierdzony kanał wiadomości. WMO211 unexpectedHTTP.
Nie dodano obejścia polityki, nowych hostów ani aktywacji tych dwóch źródeł.
