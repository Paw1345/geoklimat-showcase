# BDL — ręczny przebieg RDLP z trwałym kursorem

Aktualizacja2026-10-04T11:23+02:00: [zatwierdzenie i pełny przebieg RDLP](BDL-SCAN-APPROVAL.md)
zakończone.25manualapproved/disabled;113complete17/17/78414771B, eksporty/SHA/
manifest/noGETrepeatPASS. Zastępuje niższe unapproved/0copies tylko dla tego profilu.
Nie pełnyBDL/4.5; zwykły worker zastany zatrzymany i nie uruchomiony.

Aktualizacja 2026-10-03T18:55Z: tryb dużej geometrii i status blokady są
zaimplementowane. Poniższe historyczne „pozostają do wdrożenia” zastępuje ten
opis, ale kanał25 nadal disabled/unapproved/0copies/0harvests. Brak importu.

## Duża odpowiedź na jawne żądanie operatora

```powershell
# Odczytowa próba następnego przypiętego id, bez zapisu:
docker compose exec -T web python manage.py bdl_scan --fetch --on-demand
# Po osobnym zatwierdzeniu kanału: jeden zapis i przesunięcie tego samego kursora:
docker compose exec -T web python manage.py bdl_scan --fetch --on-demand --apply
```

Nie ma automatycznego fallbacku. Bez --on-demand nadal obowiązuje limit
min(5MiB, DATA_AUTO_MAX_BYTES); jawny tryb ma min(50MiB, DATA_MANUAL_MAX_BYTES).
OpenAPI/manifest zachowują dotychczasowe małe limity. Cache+rezerwa1GiB,
licencja/robots/profil/zgoda/id/rok/manifest przed i po GET, retencja30dni,
oryginalne bajty oraz atomowy snapshot+kursor pozostają obowiązkowe.

Rozpoznany komunikat transportu `Response exceeds size/time budget` przy
rzeczywistym zapisie tworzy/oznacza przebieg blocked, bez kopii i bez zmiany
records/pages/next_url. Ponowna kontrola zgody/profilu/kursora chroni ten zapis
diagnostyczny. Dry-run nie zapisuje nawet statusu blocked. Innych błędów
(tożsamość, schema, geometria, robots, HTTP) nie klasyfikujemy jako powodu
większego pobrania. Nie rozróżniamy automatycznie przyczyny rozmiar/czas.

Przebieg blocked bez --on-demand zwraca status bez kolejnego GET. Jawne
ponowienie używa tego samego id/kursora i świeżego manifestu. Udany zapis usuwa
blokadę i przesuwa kursor o1; nieudany większy GET nadal pozostawia blokadę.
Status blocked, link do obiektu i wyjaśnienie są widoczne w szczegółach kanału;
staff widzi instrukcję CLI. Nie dodano przycisku uruchamiającego pobranie w WWW.

Żywa odczytowa próba id2: 8294135B, rok2026, dokładny self/1obiekt/geometria
PASS w trybie on-demand, bez zapisu DB. To potwierdza konkretny obiekt większy
niż5MiB, nie poprawność wszystkich17 geometrii. Kanał25 pozostaje niezatwierdzony.

6 nowych testów/regresji: blocked bez automatycznych GET, ponowienie tego samego
id, brak zapisów diagnostyki w dry, schemabłędy bez reclassify, revoke guard,
limit50MiB i oddzielny auto, CLIflag guard, UI/noGET. Istniejący test strony
rozszerzono o odpowiedź6MiB akceptowaną tylko on-demand.14scanPASS0.210s,
888fullPGPASS51.444s/Ruff430/check/nomigrations/buildPASS. Brak restartów,
aktywacji, importów, zmian cache15GB/retencji30/próbek22–24/GitHub.

Następny etap: chronione zatwierdzenie dostępu do ręcznego profilu po świeżych
próbach/budżecie, rzeczywisty zapis/wznowienie/export i dowód zachowania id2.
CałyBDL/epik4.5 nadal otwarte; nie traktować kopii jako snapshotu jednoczasowego.

2026-10-03T18:47Z. Mechanizm zaimplementowany i przetestowany, kanał25
`bdl-rdlp-pinned-scan` przygotowany disabled/unapproved, 0 kopii/0 przebiegów.
Nie uruchomiono trwałego importu. Próbki22–24 nadal active/approved.

## Obsługa

```powershell
docker compose exec -T web python manage.py bdl_scan
docker compose exec -T web python manage.py bdl_scan --prepare
docker compose exec -T web python manage.py bdl_scan --prepare --apply
docker compose exec -T web python manage.py bdl_scan --fetch
# Dopiero po osobnym zatwierdzeniu access_approved dla tego ręcznego profilu:
docker compose exec -T web python manage.py bdl_scan --fetch --apply
```

Domyślnie brak HTTP/zapisów. Prepare dry nie zapisuje, prepare apply tworzy tylko
brakujący wyłączony profil i nie zmienia zgód/historii administratora. Fetch bez
apply sprawdza jeden obiekt bez zapisu kopii/kursora. Fetch apply wymaga
access_approved=true, ale enabled=false: to profil ręczny, nie automatyczny.
Nie dodano komendy zatwierdzającej. Generic audit_data i collect_data odrzucają
ten tryb; nie należy aktywować go zwykłym enabled. Wszystkie wywołania operacyjne
korzystają ze wspólnej blokady kolektorów, busy oznacza brak zmian.

Jedno wywołanie pobiera maksymalnie jedną geometrię. Świeży manifest przed/po
GET musi zachować hash posortowanych id/lat. Żądanie używa dokładnego id zamiast
offsetu: `/collections/rdlp/items?f=json&limit=1&id=N`. Wymagane są dokładne
liczniki1/1, feature.id, rok danych, geometria, self i brak next. Budżet geometrii
nie przekracza mniejszej wartości 5 MiB / DATA_AUTO_MAX_BYTES. Manifest ma
max100id/256KiB, OpenAPI5MiB. Jeden krok wymaga też co najmniej 1GiB rezerwy cache.

Żaden SQL transaction nie obejmuje HTTP. Po odczytach następuje transakcja:
blokada cache, ponowna kontrola profilu/zgody oraz starego kursora i pojemności,
zapis niezmienionych bajtów DataSnapshot + przesunięcie DataHarvest. Błąd zapisu
cofa obie operacje. Retencja kopii30dni, SHA256, źródło/licencja/id/rok/czas
pobrania w metadanych. Export weryfikuje SHA/rozmiar/id/url/licencję i zachowuje
oryginalne bajty; udostępnia attribution/license/canonical headers i opis w UI.

Wznowienie używa już zapisanego kursora i nowego manifestu o tym samym hashu.
Zmiana zbioru/roku zatrzymuje przebieg do przeglądu; nie resetuje historii.
Complete nie uruchamia nowego przebiegu i nie wykonuje kolejnych GET.
Complete oznacza przejście przypiętych id, nie jednoczasowy snapshot dostawcy.
Każda kopia nadal partial/full_dataset_coverage=false.

## Duże geometrie i pozostałe zadania

Błąd pobrania nie przesuwa kursora i nie pomija obiektu. Transport potrafi
zgłosić wspólny błąd rozmiaru/czasu — nie traktujemy go automatycznie jako
potwierdzenia rozmiaru. CLI wskazuje konieczność przeglądu profilu/polityki/
manifestu/budżetu. Brak automatycznego fallbacku zwiększającego limit.

Osobna ścieżka większych geometrii na żądanie, powiązanie jej wyniku z tym samym
id/rokiem/kursorem oraz oznaczenie blokady w UI pozostają do wdrożenia.
Nie ma jeszcze aktywacji ani produkcyjnego dowodu trwałego importu/wznowienia
RDLP. Nie obejmujemy nadleśnictw/leśnictw/wydzieleń i nie zamykamy pełnegoBDL/4.5.

## Weryfikacja

8 nowych testów PostgreSQL: rawcopy/export/resume/complete noGET, dry noDB,
manifest drift, budget failure, revoked approval, transaction rollback,
cachepreGET, profilepreserve/genericenableguard, exact-ID schema/geometry.
36 testów BDL PASS0.439s; pełne882PASS50.201s. Ruffcheck/format429PASS,
Djangocheck/nomigrations/buildwebworkerPASS. Build nie oznacza restartu/wdrożenia;
ostrzeżenie pip-root istnieje podczas budowania, nie instalowano nic na hoście.

Live exact-ID1 GET: 2331884B, rok2026, geometrywalidacjaPASS, bez DB.
Prepare początkowo busy, późniejszy dry+applyPASS, kanał25 disabled/unapproved.
Końcowy fetchCLI ponownie busy, brak kopii/kursora. Nie wymuszano blokad ani
restartów; brak zmian limitu15GB/retencji30/próbek22–24/GitHub/XLS.
