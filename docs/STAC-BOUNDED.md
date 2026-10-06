# STAC — osobny wznawialny przebieg z budżetem

Pakiet4.5.8/4.5.9, 2026-10-03. Supersedes etapdisabled poniżej:
profilePL20/UE21 **aktywne po świeżych próbach i kontroli budżetu**.
Worker potwierdził wznowienie tych samych przebiegów: PL68 complete4strony36rekordów;
UE69 bounded16stron160rekordów11986462B w kontroli odczytowej, z zachowanym
kursorem. Rezerwacja całej strony5MiB powoduje poprawny stop poniżej16MiB.
[Dowody aktywacji i odświeżenia workera](STAC-BOUNDED-ACTIVATION.md).
Nie deklarujemy pełnej doby ani zamknięcia epika4.5.

## Profil i zakres

Tryb `stac_bounded`, osobne kanały od historycznych4/6 i próbek18/19.
Wyłącznie ten sam oficjalny STAC GET, kolekcja Sentinel-2L2A i zatwierdzone bbox
rodziców: PL `[14.12,49.0,24.15,54.84]`, UE `[-31.3,27.5,35.7,70.1]`.
Dokładne profile, hash rodzica, rejestr, URL, kolekcja i interwał1440min są
pilnowane; zmiany administracyjne powodują odmowę, nie nadpisanie.

- Okno24h zamrożone na starcie; strony po10scen, dwie na partię.
- Na jedno okno najwyżej20stron/200wystąpień oraz16MiB znormalizowanych payloadów.
  To nie budżet całego ruchu HTTP ani gwarancja200różnych scen.
- Każda kolejna strona wymaga wolnego całego maksymalnego budżetu strony5MiB
  w limicie okna. Zatrzymanie może nastąpić **przed wykorzystaniem pełnych16MiB**;
  nie wykonujemy GET wyłącznie po to, by sprawdzić, czy zmieści się mniejsza strona.
- Po aktywacji nowe okna planowane co dobę; `collecting` kontynuuje istniejące
  co30sekund, nie co dobę. Interwał jest harmonogramem, nie gwarancją czasu ukończenia.
- Nie pobieramy rastrów ani assets. Bbox nie jest dokładną maską administracyjną.

## Stany i zachowanie kursora

| Stan przebiegu | Znaczenie | Dalsze działanie |
|---|---|---|
| running / kanał collecting | Są kolejne strony, budżet nieosiągnięty | Ten sam przebieg i zamrożone okno |
| complete | Brak linku następnej strony | Następne zaplanowane okno po dobie |
| bounded | Osiągnięty limit stron lub rezerwy rozmiaru strony w oknie | Kursor zachowany; następny cykl zaczyna nowe okno, nie wznawia bounded |
| paused | Brak miejsca ponad rezerwę tego kolektora | Kursor i kopie zachowane; próba wznowienia po interwale |
| failed | Błąd pobrania/walidacji/zapisu | Ostatnia zatwierdzona strona i kursor zachowane do ponowienia |
| expired | Koniec okna starszy niż48h | Stary przebieg/kursor/kopie zachowane; osobny nowy przebieg |

Complete nie oznacza pełnej doby dostawcy, aktualnych30dni, globalnej unikalności
ani jednoczasowej wersji scen. Expired nie usuwa kopii; obowiązuje zwykła retencja30dni.
Ręczne wymuszanie kolejnych przebiegów nie jest zaplanowanym sposobem eksploatacji.

## Zapis, rezerwa i zgody

Przed GET kontrolujemy miejsce na maksymalną stronę ponad1GiB rezerwy.
Po GET, w transakcji, blokujemy rodzica i bieżący profil, ponownie sprawdzamy
konfigurację i zgodę. Cofnięcie zgody w trakcie pobrania odrzuca zapis.
Następnie pod tą samą blokadą cache726043 co wszystkie zapisy snapshotów
sprawdzamy `SUM(size_bytes) + rozmiar strony +1GiB <= DATA_STORAGE_MAX_BYTES`.
Strona, liczniki, fingerprinty, status i kursor zatwierdzają się atomowo.
Brak rezerwy zatrzymuje ten kolektor bez zapisu strony/przesunięcia kursora;
błąd zapisu wycofuje stronę i zachowuje poprzednio zatwierdzony stan.

**Rezerwa dotyczy tego kolektora**, nie jest oddzielnym magazynem ani globalną
polityką wszystkich innych kanałów. Inne kolektory mogą ją wykorzystać;
limit15GB i zwykłe zabezpieczenie wspólnego cache pozostają bez zmian.
Nie mierzymy fizycznego dysku, indeksów/WAL/Docker ani odzyskania po retencji.

JSON bez duplikatów kluczy/NaN/Infinity, kolekcja, świadome daty w oknie,
struktura geometrii, profil/query/host/robots/DNS i pętle stron pozostają
sprawdzane. Dopuszczamy wznowienie zamrożonego okna do48h od jego końca;
świeże próbki nadal wymagają końca zapytania nie starszego niż10minut.

## Konfiguracja bez aktywacji

```text
docker compose exec -T web python manage.py configure_stac_bounded --wait-seconds 30
docker compose exec -T web python manage.py configure_stac_bounded --apply --wait-seconds 30
```

Domyślnie dry-run. `--apply` tworzy tylko brakujące profile jako disabled/unapproved;
nie ma opcji aktywacji ani HTTP. Powtórzenie zachowuje ustawienia/historię.
Blokada zajęta przez najwyżej30s kończy polecenie bez zmian; brak forsowania workera.
Konfiguracja dwóch profili jest wspólnie atomowa, odmienne ustawienia administratora
powodują odmowę całej operacji. Osobna chroniona aktywacja wymaga świeżych prób,
oceny cache i odświeżenia workera; wykonano ją według dokumentu aktywacji.

## Sprawdzone wyniki

Scenariusz pojemności15:00:25Z: zajętość9871884760B/15GB, dodatkowe32MiB/dobę
łącznie×30=1006632960B; rezerwa1073741824B; przyszłe próbki przy stałej ostatniej
wielkości2157976800B. Scenariusz mieści się dziś także z próbkami, lecz wzrost
innych kolektorów i pełny wolumen dostawcy pozostają nieznane; zgoda aktywacjifalse.

Pierwszy liveapply zwrócił busy/nochanges; następny utworzył PL20/UE21 wyłączone
i niezatwierdzone. Odczyt PostgreSQL:0przebiegów/0kopii każdego, publiczne karty
HTTP200 przez RequestFactory; staffSTACJSON200/6kanałów. Pięć usług running/DBhealthy.
Końcowa próba livepowtórzenia konfiguracji także trafiła na busy/nochanges;
idempotencję potwierdzają testy, nie udany livepowtórny zapis. Nie forsowano blokady.

13nowych testów: wznowienie, atomowy rollback, druga strona po awarii, limit20stron,
limitbajtów, reservepreGET/poGET, cofnięcie zgody w trakcie, frozen30min/expiry49h,
cadence, disabled/profileguard, dryapply/repeat/adminpreservation.
23celowane przed dwoma ostatnimi testami PASS3.859s;
końcowe **831testów PostgreSQL PASS45.589s**, Rufflint/format403plików,
Djangocheck/brak nowych migracji/buildwebworker PASS.
Pierwszy test integralności używał czasu przed utworzeniem kopii; poprawiono fixture
na aktualny czas weryfikacji, bez zmiany zasad ważności kopii.

Dokumentacja i karty rozróżniają bounded/paused/complete. [Integralność](STAC-INTEGRITY.md),
[katalog GIS](STAC-SCENES.md), [audyt](STAC-AUDIT.md) i [pokrycie](STAC-COVERAGE.md)
rozpoznają nowe okno24h przez wspólny profil zakresu, bez podnoszenia flag globalnych.
Bez migracji schematu, resetu historii/próbek, restartów, zmiany XLS/retencji/quota/push.
Etapdisabled/auditpending opisany wyżej jest historyczny, superseded aktywacją.
Automatyczny postęp, integralność i GeoJSON potwierdzone dokumentem aktywacji.
Następny krok: ocena końcowego zakresu UE po zwykłym budżetowym przebiegu oraz
pozostałe braki epika4.5, nie ponowna aktywacja/reset ani zwiększanie limitów.
