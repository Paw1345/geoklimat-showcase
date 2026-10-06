# DG Environment — trzy grupy tematyczne (rejestr 165)

Źródło: https://environment.ec.europa.eu/index_en, oficjalny odsyłacz English
z wyboru języka pierwotnego endpointu https://environment.ec.europa.eu/.
Ten sam host transportu, bez nowej allowlisty lub wyjątku robots.
Interwał 240 minut.

Zakres: trzy karty nawigacyjne Circular economy, Pollution, Nature and Biodiversity.
Każda obejmuje własny odsyłacz strategii i listę tematów: aktualnie 3+18=21 rekordów.
Nie czytamy menu, innych bloków strony, wiadomości ani pełnych aktów.
Kanał nie jest pełnym katalogiem polityk lub zmian prawnych.

Tytuł, rola group/topic, kategoria i URL pochodzą z indeksu.
Daty publikacji/aktualizacji pozostają nieznane. Rok 2030 w nazwie strategii
nie jest datą publikacji. Opis grupy, jeśli występuje, zapisujemy tylko
przy grupie; nie przypisujemy go wszystkim tematom. Obecnie grupy mają
puste opisy, więc nie generujemy streszczeń ani treści zastępczej.
Brak domysłów o autorach, stanie prawnym lub naborach. UI ujawnia zakres.

Parser wymaga endpointu/profilu, UTF-8, title/h1/main, dokładnie trzech znanych
kart i zgodnych linków strategii. Jedna lista i opis grupy, 1–50 tematów/grupę,
łącznie najwyżej150rekordów. Cała partia walidowana przed zwróceniem:
bez duplikatów, nieznanych elementów listy i dodatkowych odsyłaczy.
Tożsamość URL niezależna od tytułu, kolejności i względnej/absolutnej formy linku.
Zmiana kategorii/roli/opisu jest zmianą rekordu, nie nowym identyfikatorem.

Dopuszczone tylko HTTPS environment.ec.europa.eu/strategy|topics/*_en
z maksymalnie jednym podpoziomem i jeden konkretny odsyłacz „Sustainable products”
na commission.europa.eu (EXTERNAL_LINK w kodzie). To tylko link: nie dodaje
transportowych uprawnień, nie pobiera stron docelowych, PDF lub obrazów.
Nie akceptujemy query/fragmentów, innego hostu lub niezweryfikowanej ścieżki.

configure_environment_policies dry-run domyślnie/noHTTP/lock+transaction,
tylko pierwotne nieużywane wyłączone registry-165. PK/admin/history/linkage/
owner/name/240min zachowane; repeat zachowuje audyt i zgodę. Workbook/cache/
schema/kursory bez zmian. Testy/fresh audit/dry import/activation przed apply,
worker odświeżany tylko SIGTERM/naturalexit0 i potem unless-stopped.

## Dowody

2026-10-02: DockerOFF; wznowiono istniejący Desktop hidden/dbweb bez resetu danych.
Fresh officialGET200/parser21/0, konfiguracja pierwotnaPK126/0items/0runs.
11focusedPASS0.295s: grupy+tematy/datynieznane/brakstreszczeń, opisytylkogrupy,
externalnochildGET, stableURL/titleorder, schemat/linki/duplikaty/budżety,
profile/HTTP i configdryapplyrepeat/admin/history/linkage/UIescaping.
2026-10-02T07:09Z: 11focused/577full PostgreSQL PASS19.990s,
Ruff check/format310files/Django check/no migrations/buildPASS.
Po starcie Dockera worker był już exited137 sprzed10h, Ollama/AI exited0;
nie wysyłano w tym kroku SIGTERM/SIGKILL i nie przypisano historycznego137
aktualnej operacji. Configdry/apply/repeat preservedPK126/admin/linkage/240min.
Fresh audit200/21/0, dry import21/0, eligible1dry przed apply.
Import21created; niezależny drugi HTTP200:21unchanged/0created/0updated.
21published/source_updatedNULL,3groups18topics/fail0.
Worker wznowiony07:09:23Z/unless-stopped, manifest
24d46ce2023a8c780fc103cd38891ff96695d1a1917a90f284d9be0941c884f2;
existing Ollama/AI started,5running/DBhealthy.
Ready+registry165HTTP200, Item15020 UI grupa/dateunknown.
196Sources164active32disabled/cache15GB/full34businessgaps unchanged.
Brak resetu danych, kursorów, workbook, migracji lub nowych transporthosts.
