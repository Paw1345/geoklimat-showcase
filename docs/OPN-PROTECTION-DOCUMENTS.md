# OPN — katalog dokumentów ochronnych

Przegląd2026-10-02: oficjalna strona
https://opn.gov.pl/dzialalnosc-ochronna zawiera sekcję
„Dokumenty związane z działalnością ochronną”: procedurę nadzoru nad
lasami niepaństwowymi, rozporządzenie dotyczące planu ochrony oraz
mapę leśnych zbiorowisk roślinnych. Osobna sekcja wskazuje plan
ochrony w ISAP (WDU20210001432).

## Zakres i ograniczenia

Nowy Source `registry-151-protection-documents` przypisany do registry151,
odświeżanie1440min. Pierwotny kanał news PK113/config/interval360/
historia/approval pozostają bez zmian. Workbook nie jest modyfikowany.
Konfiguracja: `{"strict_endpoint": true, "opn_documents": "protection-catalogue"}`.

Każdy PDF: nazwa, link otwarcia, deklarowany rozmiar i typ.
Pozycja ISAP: odnośnik oraz identyfikator, nie treść aktu.
Daty publikacji/aktualizacji NULL; bez wnioskowania z roku aktu lub
prozy opisującej ochronę. Rozmiar z katalogu to tekst deklarowany,
nie zmierzona wielkość ani gwarancja integralności.
Brak pobierania PDF, stron ISAP, map, obrazów, galerii lub archiwum.
Użytkownik może otworzyć odnośnik źródłowy; nie jest to downloader
plików do lokalnego cache.

Nie potwierdzamy obowiązywania prawa, kompletności planu, zmian
wewnątrz PDF ani wszystkich konsultacji/BIP. Monitorowane są zmiany
metadanych katalogu: plik zmieniony pod tym samym URL bez zmiany
metadanych nie zostanie wykryty. To częściowe pokrycie dokumentów
ochronnych, nie zamknięcie pełnego zakresu registry151 lub epika4.5.
UI pokazuje ograniczenia i nieznane daty.

## Zabezpieczenia

Dokładny endpoint/profil/web/GET200/UTF8/main/h1/sekcje i tytuły.
1–100 pozycji PDF, unikalne kanoniczne UUID, sparowane URL otworz/
pobierz, dokładnie dwa linki w pozycji, ograniczone nazwy i format
rozmiaru. Odnośnik ISAP dokładny, display-only; brak nowych hostów
transportu/wyjątków robots. Zmiana aktu/struktury zatrzymuje całą
partię i wymaga przeglądu. UUID to klucz; nazwa/pozycja listy nie
zmienia tożsamości dokumentu.

`python manage.py configure_opn_documents`: dry-run/no HTTP;
`--apply` tworzy osobny wyłączony kanał w transakcji i pod batch lock.
Sprawdza pierwotny profil news i powiązania registry151.
Kopiuje owner/topics/language/priority; nie zmienia news ani historii.
Powtórzenie zachowuje audyt/approval/historię, obce konfiguracje i
powiązania odrzuca. Pierwotny configure_opn_news ma celowo ścisłą
kontrolę jednego powiązania; po dodaniu companion nie należy ponawiać
tej zakończonej migracji (odmówi bez zapisu).

Aktywacja wyłącznie po świeżym audit_sources, ingest --dry-run i
activate_registry bez --apply, ograniczonych do nowego slug.

## Walidacja

9 tests katalogu (w tym scoped activation), pełne616PostgreSQLPASS20.289s/
finalRuff327/check/nomigrations/buildPASS. Live GET200:4/0
(3PDF+1ISAP), daty niepodane.
Testy UUID/zmian nazw/kolejności, duplikatów/budżetu/URL/rozmiaru,
profilu/statusu, dry/apply/repeat/noHTTP, historii news, administratora
i escapowania UI.

2026-10-02T08:49Z: pierwsza próba aktywacji0 ujawniła filtr
slug__startswith registry-. Wyłącznie nowy pusty Source197/disabled/
unapproved/0items/0runs przemianowany z opn-protection-documents na
registry-151-protection-documents, bez zmiany PK/powiązań/obcych danych.
Dodano test aktywacji pod tym slug; świeży audyt powtórzono.
Audyt verified4/0, dry import4/0, dry activation1, następnie apply.
4created, niezależne ponowne pobranie4unchanged/0created/0updated/
0failures. Daty publikacji/aktualizacji NULL, date_precision unknown.
Item15132–15135; widoki PDF/ISAP HTTP200 z ograniczeniami i nieznanymi
datami, registry151/health-ready HTTP200.
PrimarynewsPK113 activeapproved360min/12items bez zmian.
Worker SIGTERM naturalexit0 o08:44:14Z, wznowiony08:48:23Z/
unless-stopped/manifest4192f7e7…d0dd595.
197Sources168active29disabled/5runningDBhealthy/cache15GB bez zmian.
