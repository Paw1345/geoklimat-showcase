# IPCC — początkowe karty katalogu raportów (rejestr 215)

Oficjalny katalog: https://www.ipcc.ch/reports/.
Zakres to początkowe karty statycznego HTML, nie pełne archiwum, paginacja
ani analiza treści naukowych. Kolektor nie pobiera szczegółów, ilustracji,
PDF-ów lub załączników. Harmonogram pozostaje 1440 minut.

Katalog obejmuje także zapowiedzi raportów. Miesiąc/rok na karcie jest
zapisany w metadata jako tekst źródłowy, YYYY-MM i precyzja month.
published/updated pozostają puste: nie dopisujemy pierwszego dnia miesiąca,
nie bierzemy roku z tytułu, nie potwierdzamy publikacji z samego linku.
publication_status_verified=False jest jawne w widoku wpisu.
Nie dodajemy streszczeń, autorów ani ocen naukowych z materiału, którego
nie pobrano.

Parser wymaga dokładnego endpointu i profilu, UTF-8, tytułu Reports — IPCC,
h1 Reports, body#page-reports oraz właściwej siatki katalogu.
Jedyny niebędący kartą element siatki to jeden pusty div.grid-sizer.
Budżet: 1–100 kart. Cała partia wymaga unikalnych HTTPS www.ipcc.ch/report/
adresów, dokładnych pojedynczych pól typu/tytułu/miesiąca, zgodnego
przycisku Explore i dwóch odsyłaczy na kartę. Nie czytamy menu jako raportów.
Nie śledzimy przycisków ani dodatkowych stron. Klucz to stabilny slug
raportu, bez tytułu, terminu i kolejności. Zmiana pola aktualizuje rekord.

configure_ipcc_reports: dry-run domyślnie, bez HTTP, wspólny lock i transakcja.
Zmiana dotyczy tylko pierwotnego nieużywanego wyłączonego registry-215.
Historia, administrator, powiązanie rejestru, PK, nazwa, właściciel,
interwał i master XLSX są chronione. Ponowne wykonanie zachowuje audyt
i zatwierdzenie. Aktywacja dopiero po testach, świeżym audycie i dry-run.
Odświeżenie tylko workera: SIGTERM i naturalne exit0, bez resetu/SIGKILL.

## Dowody

2026-10-01: oficjalny GET200/parser10/0, bez zapisu rekordów.
10 testów parsera/konfiguracji/UI PASS: przyszłe i historyczne miesiące,
daty nieznane, tożsamość, zmiany tytułu, budżety, duplikaty, host/linki,
schemat, dry/apply/repeat/noHTTP/admin/history/linkage i escaping UI.
Pierwsza próba wykryła dodatkowy pusty element grid-sizer; dopuszczono
wyłącznie ten element, bez ogólnego pomijania nieznanych dzieci siatki.
Test źle domkniętego HTML zmieniono na test dwóch pól miesiąca:
BeautifulSoup naprawia niezgodną etykietę zamykającą, więc nie był to
rzeczywisty przypadek niejednoznaczności DOM.
2026-10-01T18:24Z: 10focused/532full PostgreSQL PASS19.455s,
Ruff check/format294files, Django check, brak migracji i buildPASS.
Configure dry/apply/repeat zachował PK156/registry215/linkage/1440min.
Fresh audit200/10/0, dry import10/0 i dry activation1 przed apply.
Import10created; niezależny drugi HTTP200:10unchanged/0created/0updated.
Wszystkie 10 published_at/source_updated_at NULL, failures0.
Worker SIGTERM naturalexit0, wznowiony18:23:38Z/unless-stopped,
manifest167e42adc60fc265c7c919f8d4392f179eff9bb7d29cfe5512edb2d1db04c2b7.
5running/DBhealthy, GUGiKharvest42 nadal postępuje224pages/22400records.
Ready/rejestr215/Item14922HTTP200; UI zapowiedzi i niepotwierdzonego statusu.
196Sources160active36disabled/cache15GB bez zmian; full34gaps4.5open.
Pomyłka w odczytowej kontroli nazwy updated_at skorygowana do
source_updated_at; nie zmieniano schematu ani danych.
