# Cache danych — pojemność i retencja

Pakiet epika 4.5.9. Panel staff `/data/storage/`, eksport
`/data/storage/?format=json` i CLI `python manage.py cache_report` korzystają
z jednego odczytowego raportu. Link jest w katalogu `/data/`.

## Zasady

- PostgreSQL REPEATABLE READ READ ONLY; dwa zapytania SELECT, maksymalnie 1000 kanałów.
- Nie pobieramy danych przez HTTP, nie przenosimy ani nie usuwamy kopii,
  nie zmieniamy zadań, harmonogramów, kursorów, retencji ani limitu.
- Zużycie deklarowane to suma `size_bytes`, zgodna z obecnym mechanizmem quota.
  Rzeczywista długość treści to suma `OCTET_LENGTH(payload)` obliczana w bazie.
  Treści nie są eksportowane do raportu ani wczytywane jako obiekty Python.
- Wszystkie zapisane kopie, również wygasłe i części nieukończonych pobrań,
  nadal zajmują miejsce. Fragmenty plików są podzbiorem sumy, nie dodatkową pulą.
- Wygasanie kopii sprawdzamy względem `expires_at`, twardego okna od `fetched_at`
  i terminu całego zadania plikowego. Równość granicy oznacza wygasanie.
  Raport NIE sprawdza dat poszczególnych obserwacji w mieszanych stronach.
- Rozbieżność deklaracji i rzeczywistej długości jest flagą przeglądu, nie
  automatyczną naprawą. Wolne bajty logiczne nie są wolnym miejscem na dysku.
- Daty od–do opisują pobranie niewygasłych kopii, nie okres pomiarów, pełne
  30 dni danych, integralność SHA256 ani dostępność pełnego eksportu.
- Nie mierzymy indeksów, WAL, backupów, wag AI ani wolnego dysku Windows/Docker.
- HTML jest escapowany; staff-only, GET-only, no-store; JSON nosniff.
  Raport nie ujawnia URL, konfiguracji, właścicieli ani surowych błędów kanałów.

## Weryfikacja 2026-10-03T09:48Z

7 nowych testów i pełne 728 testów PostgreSQL PASS (28.881 s).
Ruff lint/format (368 plików), Django check i brak nowych migracji PASS.
Docker build web PASS; standardowe ostrzeżenie pip o użytkowniku root dotyczy
instalacji podczas budowy obrazu, nie nowego błędu aplikacji.
HTML i JSON renderowane na bieżącej bazie: 200, Cache-Control no-store.
Wszystkie pięć istniejących usług działa; nie restartowano workera.

Odczyt o 09:48:11Z: 18769 kopii, 9638257792 bajty zarówno deklaracji, jak
i rzeczywistej długości treści, 0 rozbieżności. Limit 15000000000 bajtów;
pozostało 5361742208 bajtów. Jedna wygasła kopia (3099 bajtów), jeden fragment
pobrania (754688 bajtów). To datowany stan przy działającym workerze, nie
stała obietnica wolnej przestrzeni. Niczego nie usunięto na podstawie raportu.

## Pozostałe kryteria

Ten pakiet nie zamyka całego 4.5.9 ani epika 4.5. Nadal wymagane są:
odbiór rzeczywistej historii dla uzgodnionych produktów, kompletność STAC,
rozstrzygnięcie zakresów źródeł i końcowy odbiór biznesowy.
Do sprawdzania pełnych kopii PIG używaj istniejącego `data_coverage --require-verified`;
do dat obserwacji IMGW — panelu historii pomiarów. Nie interpretuj liczby kopii
cache jako liczby dni kompletnego szeregu.
## Scenariusze rozszerzenia STAC

Uzupełnienie2026-10-03: [STAC-CAPACITY](STAC-CAPACITY.md) korzysta wyłącznie
z sumy deklarowanych rozmiarów i scenariuszy retencji. Nie zastępuje poniższej
kontroli rzeczywistych bajtów/rozbieżności, nie mierzy dysku i nic nie usuwa.
