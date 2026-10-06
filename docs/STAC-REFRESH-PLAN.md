# STAC — świeże okno bez resetu historii

Stan: 2026-10-04T12:10+02:00. Raport wdrożony lokalnie; nie jest nowym kolektorem
ani zgodą na rozszerzenie pobierania. Zastępuje propozycję tworzenia kolejnego
kanału UE: istnieją już aktywne kanały recent oraz bounded dla obu regionów.

## Dostęp i zakres

Panel staff GET `/data/stac/`, sekcja „Plan świeżego okna — bez resetu historii”.
JSON tego samego panelu zawiera `refresh_plan`. CLI:

```powershell
docker compose exec -T web python manage.py stac_refresh_plan
```

Nie ma opcji apply/fetch/force. CLI i panel używają PostgreSQL REPEATABLE READ
READ ONLY. Odczytują wyłącznie profile, metadane ostatnich przebiegów i sumę
deklarowanych rozmiarów kopii. Nie czytają payloadów, nie wykonują HTTP, nie
tworzą kanałów, nie aktywują ich i nie zmieniają harmonogramu ani historii.
Raport ogranicza liczbę kanałów STAC do20. Nie eksportuje surowych URL/tokenów,
konfiguracji, błędów ani danych użytkowników.

## Decyzja dla regionu

Każdy z PL/EU ma trzy oddzielne pozycje: stary przebieg, próbkę recent24h oraz
bounded24h. Raport sprawdza istniejące strażniki profilu, parent oraz hash i
zakres dat ostatniego przebiegu. Pokazuje następny termin normalnego workera
i czy termin już przypada, ale nie gwarantuje wykonania.

Poprawny aktywny bounded -> `reuse_scheduled_bounded`, `new_channel_needed=false`.
Brak/cofnięta zgoda/zmieniony profil lub zakres -> `review_existing_profile`,
bez automatycznego tworzenia zamiennika. Brak profilu nie jest kosztem działającego
kolektora, a nieznany koszt aktywnego profilu pozostaje null, nie zero.

Daty candidate_search_start/end to ilustracja świeżego24h w chwili raportu.
`candidate_window_pinned=false`: rzeczywiste okno zostaje zamrożone dopiero
przy normalnym rozpoczęciu przebiegu. `complete` oznacza koniec konkretnego
zapytania, nie pełne obecne30dni. Missing_scene_count=null; liczby brakujących
scen nie da się wywnioskować z samych metadanych zakresu. Nie dowodzimy SHA,
ciągłości, unikalności, dokładnej maski PL/UE ani pobrania rastrów/assets.

## Wspólny scenariusz budżetu

Nie dodajemy ponownie budżetu nowych kanałów, które już działają:

- Próbki: wielkość ostatniego świeżego poprawnego przebiegu × liczba cykli/dobę
  × okres retencji, wspólna logika stac_capacity. To oszacowanie, nie pełny wolumen
  dostawcy ani gwarantowany pułap.
- Bounded: istniejący pułap16MiB/okno × liczba cykli/dobę × retencja.
- Porównanie: obecne kopie + przyszłe próbki + przyszłe bounded + rezerwa1GiB
  względem quota15GB. Wygasłe kopie nadal liczą się, dopóki normalna retencja
  ich nie usunie; nie odliczamy przyszłego wygasania z góry.
- Nieznane aktywne bounded lub niewiarygodne aktywne próbki powodują null
  scenariusza. Wzrost pozostałych kolektorów i miejsce na dysku pozostają nieznane.

Przekroczenie scenariusza nie przewiduje daty zapełnienia cache: próbki mogą
zmieniać rozmiar, a realna retencja zwalnia miejsce. Raport nie wyłącza istniejących
kolektorów ani nie podnosi limitu. `additional_collection_approved=false` zawsze.
Operacyjne kontrole cache każdego kolektora pozostają bez zmian.

## Potwierdzenie lokalne

11 nowych testów;32 testy odświeżania/capacity/coverage PASS5.416s.
913 pełnych testów PostgreSQL PASS53.969s; Ruffcheck/format439/checkDjango/
makemigrations--check--dry-run bez zmian. Buildweb+worker PASS; istniejące
ostrzeżenie pip-root podczas kontenerowego build, bez instalacji na hoście.

Odczyt rzeczywisty12:09+02:00: historia PL109 i UE44 retention_review;
recent18/19 aktywne, bounded20/21 aktywne. Najbliższe planowane terminy bounded:
PL2026-10-04T17:27:46+02:00, UE2026-10-04T17:37:45+02:00.
Nie uruchamiano ich wcześniej przezforce. Recent obejmuje wyszukiwanie do
2026-10-04T09:30:52/53Z, nie wszystkie sceny do tego czasu.

Cache12481540409/15000000000B, wolne2518459591B. Przyszłe próbki2157865200B,
boundedceil1006632960B, razem3164498160B plus rezerwa1073741824B:
fits_existing_collection_scenario=false. Jest to datowany scenariusz, nie
bieżąca trwała wartość ani pomiar dysku. Normalny worker kontynuuje retencję.

CLI rzeczywisty PASS. Render staff HTML/JSON przez RequestFactory z istniejącym
użytkownikiem, bez logowania/zapisu sesji, przy zabronionym Client.get:200/no-store.
Rzeczywisty HTTP anonimowy/data/stac/:302do logowania, /health/ready/:200.
To nie wizualny test zalogowanej przeglądarki. dbhealthy/web/worker running;
AI nadal wyłączone. Kod raportu jest czytany z istniejącego bind mountu web.
Nie wymieniano kontenerów ani nie restartowano workera/db/web/AI.

Nie zmieniano profili, kopii, kursorów, retencji30dni, quota15GB, XLSX ani GitHub.
Pełny STAC i epik4.5 pozostają otwarte. Następnie: odbiór świeżego przebiegu w
normalnym harmonogramie i weryfikacja jego zachowanych stron/obserwacji; dalsze
luki rejestru niezależnie, bez mnożenia kanałów ani resetu starego okna.
