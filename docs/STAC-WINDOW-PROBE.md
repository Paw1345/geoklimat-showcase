# STAC — ograniczony pomiar większej paczki

Pakiet 4.5.8/4.5.9, 2026-10-03. Diagnostyka operatora uzupełnia
[planer pojemności](STAC-CAPACITY.md). Jest odrębna od godzinnych
[próbek](STAC-RECENT.md) oraz historycznego skanowania.

## Uruchomienie w kontenerze

```text
docker compose exec -T web python manage.py stac_window_probe --region EU
docker compose exec -T web python manage.py stac_window_probe --region EU --fetch --pages 6 --budget-mib 8 --seconds 60
docker compose exec -T web python manage.py stac_window_probe --region PL --fetch --pages 2 --budget-mib 2 --seconds 60
```

Bez `--fetch` polecenie pokazuje wyłącznie plan, bez HTTP. Region wymaganyPL/EU;
domyślne limity:6stron,8MiB,60sekund na rozpoczynanie żądań.
Dopuszczalne zakresy:1–10stron (do100scen),1–16MiB,1–90sekund.
Brak dowolnego URL, kursora, parametrów bbox lub dat od użytkownika.

Wymagany aktywny, zatwierdzony i niezmieniony kanał18/19 oraz jego rodzic4/6.
Plan jest odczytywany w transakcji PostgreSQL REPEATABLE READ READ ONLY;
HTTP odbywa się po jej zakończeniu, bez blokowania workera ani otwartej transakcji.
Profil rodzica sprawdzamy ponownie przed każdą stroną. Okno24h zamrażamy na starcie,
identycznie jak świeża próbka, kolekcja Sentinel-2L2A/limit10/sortowanie od najnowszych.
Odziedziczone kontrole robots/DNS/HTTPS/hosta/query/schema/dat/geometrii nadal obowiązują.

## Zatrzymanie i raportowanie

- Po limicie stron: `page_budget`, z jawną informacją, czy pozostał kolejny link.
- Po limicie bajtów: `byte_budget`. Transport dostaje najwyżej min(5MiB,
  pozostały budżet); oddzielnie kontrolujemy znormalizowany JSON po parsowaniu.
  Przekroczenie limitu przez odpowiedź transportową jest bezpieczną odmową,
  raportowaną jako `fetch_or_validation_failed`, nie jako poprawna strona.
- Czas sprawdzamy przed i po żądaniu. `time_budget` zatrzymuje kolejne GET;
  odpowiedź zakończona po terminie nie trafia do zaakceptowanych liczników.
  **To nie twardy limit całego czasu procesu**: trwający GET/retry/robots zachowuje
  istniejące timeouty i retry transportu, nie jest przerywany siłowo.
- Pętle, zmieniony zakres, powtórzone strony i powtórzenia ID między stronami
  powodują odmowę. Liczniki zachowują tylko poprzednie poprawne strony.
- Przy braku kolejnego linku: `pagination_end`, koszt odebranych metadanych
  w `window_bytes_if_pagination_end`; nie dowodzi jednoczasowej wersji dostawcy,
  dokładnej maski ani wszystkich danych dobowych. Flagi pełnej doby/30dni/maski
  i zgody aktywacji pozostają false także dla pustej odpowiedzi.
- Błędy zwracają bezpieczny JSON i niezerowy kod CLI; brak surowych wyjątków,
  URL/kursorów, IDscen, geometrii, assets czy payloadów w raporcie.

`validated_metadata_bytes` mierzy znormalizowane poprawne strony;
`returned_response_body_bytes` liczy ciała odpowiedzi zwrócone przez transport,
także odrzucone później. **Nie są to wszystkie bajty sieciowe**: robots, retry,
przerwane odczyty i narzutHTTP nie są objęte (`all_network_bytes_measured=false`).
Budżet nie oznacza limitu wszystkich fizycznie odebranych bajtów na interfejsie.

Narzędzie nie tworzy DataHarvest/DataSnapshot, nie przesuwa kursorów ani terminów,
nie zmienia limitów/profili i nie uruchamia większych kolektorów. Odpowiedzi są
tylko w pamięci na czas próby; CLI zwraca liczby, nie umożliwia wznawiania tej próby.
Panel `/data/stac/` opisuje narzędzie, ale żaden jego GET nie wykonuje HTTP.

## Wynik rzeczywisty — 2026-10-03T14:45Z

| Region | Okno do UTC | Strony / różne ID | Metadane JSON | Ciała odpowiedzi | Czas |
|---|---|---|---:|---:|---:|
| UE | 14:45:03 | 6 /60 | 4495157B | 4283359B | 6.941s |
| PL | 14:45:11 | 2 /20 | 1498713B | 1428095B | 2.678s |

Obie próby osiągnęły limit stron, zachowały zakres, nie miały błędów i wskazały
dalszą paginację. Pełny dobowy koszt pozostaje nieznany; nie mnożymy tych liczb
w sposób sugerujący pełną dobę. Polska teraz zwróciła sceny, podczas gdy wcześniejsza
próbka13:44Z była pusta: to dwa odrębne datowane zapytania, nie korekta historii
ani dowód wcześniejszej awarii. Bez kopii w cache i bez zmian ustawień usług.

## Dalszy krok

Weryfikacja:10nowych testów,32celowane przed ostatnim testem czasu PASS5.181s;
końcowe818testów PostgreSQL PASS43.078s. Ruff lint/format399plików, Django check,
brak nowych migracji i buildweb/worker PASS. Pięć usług running, DBhealthy.
Bez restartów, zmian danych/kursorów, retencji30dni, cache15GB ani Gitpush.

Przed trwałym większym przebiegiem potrzebna jest polityka budżetu na kanał/okno,
atomowy zapis kopii i kursora z terminalnym stanem osiągniętego limitu oraz
uwzględnienie zajętości i rezerwy cache. Pełny bieżący30-dniowy zakres,
dokładna maska i odbiór biznesowy epika4.5 nadal nie są zamknięte.
