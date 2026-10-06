# STAC — chroniona aktywacja dwóch ograniczonych profili

2026-10-03 kontrola terminalna: UE69 bounded16stron160rekordów11986462B,
kursor zachowany. PL68 complete4/36/2698153B. Stop UE wynika z rezerwacji
pełnej strony5MiB przed GET; nie deklaruje całej doby ani pełnych30dni.

Pakiet4.5.8/4.5.9, 2026-10-03. Polecenie `activate_stac_bounded` jest osobne
od ogólnego `audit_data --enable`: nie zapisuje starego obiektu kanału po GET
bez ponownej kontroli profilu/zgód i wspólnego budżetu.

## Dwie fazy

```text
docker compose exec -T web python manage.py activate_stac_bounded --wait-seconds 30
docker compose exec -T web python manage.py activate_stac_bounded --apply --wait-seconds 30
```

Domyślny dry-run wykonuje świeże GET i ocenę, **bez zapisów bazy**.
Dotyczy tylko istniejących, dokładnych profili PL20/UE21, nie tworzy nowych.
Każdy nieaktywny profil wymaga próby do2stron z aktualnym oknem24h;
koniec zapytania nie może być starszy niż10minut, pomimo48h dozwolonych
dla późniejszego wznowienia aktywnego przebiegu. Pusty poprawny wynik jest
dopuszczalny, nie oznacza awarii ani pełnej doby. Wszystkie kontrole istniejącego
transportu/robots/DNS/query/schema/geometrii i pętli stron zostają.

HTTP odbywa się przed blokadą i transakcją. Następnie pod blokadą batch726042,
cache726043 oraz blokadami wierszy rodziców/kanałów sprawdzamy ponownie:

- dokładne profile, konfiguracje rodziców, interwały, nazwy i warunki wykorzystania;
- niezmienione enabled/access_approved/next_fetch_at/checked_at/last_status/last_error
  nieaktywnego kanału od przygotowania próby;
- wiek dowodu od0do10minut;
- scenariusz dodatkowych32MiB/dobę łącznie przez30dni, rezerwę1GiB i koszt
  przyszłych próbek przy ostatnim świeżym rozmiarze. `false` lub `null` odmawia.

`--apply` atomowo zatwierdza **oba** kanały: enabled/access_approved,
last_status verified, safeemptyerror, checked_at próby i next_fetch_at teraz.
Odmowa drugiego kanału wycofuje także zapis pierwszego. Nie modyfikujemy
historii, kursorów, próbek ani ustawień administratora. Próby nie tworzą kopii.
Powtórzenie dla już aktywnych, niezmienionych profili nie wykonuje GET,
nie resetuje terminów i nie zmienia checked_at. Zajęta blokada po<=30s
kończy operację bez zapisów; czekanie nie ponawia GET.

Oceniony budżet to scenariusz, nie gwarancja przyszłej zajętości ani zgoda
na szerszy zakres. Działający kolektor nadal sprawdza własną rezerwę przed
każdą stroną. Pozostały wzrost cache/dysk/pełna doba/30dni/maska niezweryfikowane.

## Odświeżenie workera

Przed pierwszą aktywacją nowych kanałów konieczny jest świeży proces workera:
stary proces sprzed dodania trybu mógłby użyć starej obsługi paginacji.
Po testach i audytach wysyłamy tylko SIGTERM do dokładnego kontenera projektu
geoklimat/serviceworker, czekamy na naturalne zakończenie partii z kodem0,
następnie zatwierdzamy kanały i wznawiamy ten sam kontener z bind mount `/app`.
Bez SIGKILL, wymuszonego stop, zmiany restart policy lub restartu web/DB/AI.
Zbudowanie nowych obrazów nie oznacza zastąpienia działających kontenerów.

## Weryfikacja kodu

7nowych testów: dryGET/no writes; atomowe2profile+repeatnoHTTP/noschedulereset;
cachefalse/null; failedprobe; secondprofileadminchange rollbackfirst;
approvaldrift; expired11min audit.19celowane przed ostatnim testem PASS3.204s;
końcowe **838testów PostgreSQL PASS46.631s**, Rufflint/format406,
Djangocheck/brak nowych migracji/buildwebworkerPASS.
Dwa niedopasowane patche nie zmieniły plików; poprawione przed testami.

Dry-run15:19Z: PL20/2strony20rekordów1498713B, UE21/2/20/1497322B,
obie dalsza paginacja, bez zapisu, scenariuszcacheTrue. Wynik datowany,
nie podstawa do późniejszej aktywacji bez nowej świeżej próby.

## Wynik aktywacji i pierwszego zapisu

Worker zakończył partię naturalnie z kodem0 o15:21:44.124Z po samymSIGTERM.
Świeży dry-run15:24:51/53Z oraz niezależne próby apply15:24:57/15:25:00Z
ponownie potwierdziły PL20rekordów1498713B iUE20rekordów1497322B/cacheTrue.
Zatwierdzono tylko kanały20/21, wspólnie atomowo.

Pierwsze jawne collect_data --apply, bezforce/resetu: PL20/przebieg68
2strony20rekordów1498713B, UE21/przebieg69 2/20/1497322B; oba running/collecting
z zachowanym kolejnym kursorem. To40wystąpień w dwóch obwiedniach, nie dowód
40globalnie różnych scen ani pełnej doby.

Ten sam kontener bc6abebb9d0b… wznowiono15:25:09.265Z, unless-stopped bez zmian,
z istniejącym bindmountrepo. Web/DB/AI nie restartowano, nie wymieniano kontenerów.
Automatyczne wznowienie potwierdzone logiem nowego workera15:27:48Z:
PL68 przesunął2→4strony,36rekordów2698153B, complete/pustykursor;
UE69 2→4strony40rekordów2995503B, collecting/zachowanykursor.
W późniejszej odczytowej kontroli UE69 miał6stron60rekordów4493921B i nadalrunning.

Kontrola pod spójną transakcją READONLY raportu: PL4/4strony iUE6/6kopii,
batch_verifiedtrue; RequestFactory cardsHTML/staffHTMLJSON/GeoJSON HTTP200/no-store,
eksportPL36/UE60obiektów. Flagi exactmask/fullcurrent30days nadalfalse.
Powtórzenie aktywacji live--apply zwróciło auditnull obu kanałów (bezGET),
bez zmian terminów/kursorów, zgodnie z testami idempotencji.
Pięć usługrunning/DBhealthy, brak restartów pozostałych usług/resetu/quota/schema/push.
UE pozostaje niepełne; nowe okna nie uzupełniają całego bieżącego30-dniowego zakresu.
