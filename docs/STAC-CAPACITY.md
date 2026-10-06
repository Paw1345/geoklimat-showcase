# STAC — budżet rozszerzenia pobierania

[STACbounded](STAC-BOUNDED.md): kod nowego profilu16MiB/okno/region,
aktywny po osobnej [chronionej aktywacji](STAC-BOUNDED-ACTIVATION.md).
Scenariusz32MiB/dobę łącznie można obliczyć CLI; sam pozytywny wynik
nie aktywuje profili ani nie mierzy wzrostu innych kolektorów.

Uzupełnienie: [stac_window_probe](STAC-WINDOW-PROBE.md) umożliwia odrębny,
ograniczony pomiar większej paczki GET bez zapisu. Wynik nie jest zapisywany
w tym planerze ani traktowany jako pełny wolumen dobowy, jeśli pozostały strony.

Pakiet 4.5.8/4.5.9, 2026-10-03. Planer jest dostępny w panelu staff
`/data/stac/`, jego JSON (`capacity`) i CLI `python manage.py stac_capacity`.
Odczytuje wyłącznie metadane PostgreSQL, w spójnej transakcji REPEATABLE READ
READ ONLY. Nie wykonuje HTTP, nie czyta payloadów, nie zmienia profili,
nie usuwa kopii ani nie uruchamia większych przebiegów.

## Scenariusz, nie prognoza kompletności

Domyślnie przyjmujemy **dodatkowe 256 MiB dziennie łącznie** dla hipotetycznych
nowych przebiegów oraz **1024 MiB rezerwy**. Parametry CLI:
`--daily-budget-mib 1..1024`, `--reserve-mib 0..16384`.
To tylko parametry obliczeń: rezerwa nie jest nową blokadą w kolektorze,
a scenariusz nie podnosi limitu cache ani nie konfiguruje pobierania.

- Zajętość: suma `DataSnapshot.size_bytes` wszystkich kopii, również wygasłych,
  dopóki faktycznie istnieją. Nie obiecujemy przyszłego odzyskania miejsca.
- Dodatkowy koszt retencji: budżet dzienny × dni retencji, obecnie30.
- Dopasowanie: zajętość + rezerwa + dodatkowy koszt ≤ limit wspólny15GB.
- Maksymalny dodatkowy budżet z aktualnego miejsca: dodatnia część
  `(limit − zajętość − rezerwa) / dni retencji`, zaokrąglona w dół.
  **Nie jest bezpiecznym limitem eksploatacji**: inne kolektory nadal rosną.
- Szacunek przyszłych próbek: rozmiar ostatniego poprawnego przebiegu ×
  ceil(1440 / interwał minutowy) × retencja. Jest dodatkowy do już zapisanych kopii,
  zakłada stały rozmiar i brak odliczenia wygaszania; nie mierzy dziennego wolumenu źródła.
- Osobno raportujemy techniczny pułap próbek przy2stronach ×5MiB na cykl.
  Jest scenariuszem maksymalnego budżetu stron, nie przewidywaną zajętością.

Szacunek próbki wymaga zgodnego profilu rodzica i hasha konfiguracji, terminalnego
stanu complete/bounded, maksymalnie2stron/20rekordów, zgodnych liczników limitów
i wieku przebiegu nie większego niż2interwały. Przy błędzie/niepełnym przebiegu,
starych danych lub zmianie profilu szacunek aktywnego kanału i dopasowanie
z jego przyszłym kosztem mają wartość `null`, nie fałszywe zero. Wyłączony kanał
nie generuje przyszłego kosztu. Pusta poprawna próbka nadal kosztuje miejsce
na odpowiedź, ale **nie oznacza zerowego kosztu pełnej doby**.

Wynik zawsze ma `activation_approved=false`, `provider_daily_volume_known=false`,
`other_collectors_growth_known=false`, `payload_integrity_verified=false`
i `disk_free_space_measured=false`. Kwota dotyczy deklarowanych bajtów payloadów,
nie indeksów PostgreSQL, WAL, obrazów Docker ani fizycznego miejsca naD:/C:.
Limity raportu: najwyżej20kanałów świeżych; nie zwracamy URL, konfiguracji,
treści payloadów ani błędów zawierających nieprzejrzane dane źródłowe.

## Odczyt rzeczywisty — 2026-10-03T14:27:52Z

| Pozycja | Bajty |
|---|---:|
| Wspólny limit | 15000000000 |
| Istniejące kopie | 9801070824 |
| Wolne logicznie | 5198929176 |
| Rezerwa scenariusza | 1073741824 |
| Dodatkowe256MiB/dobę przez30dni | 8053063680 |
| Szacunek przyszłych próbek PL18+UE19 | 1079364240 |
| Maksymalny budżet dzienny przed wzrostem innych kolektorów | 137506245 |

Scenariusz256MiB/dobę **nie mieści się** nawet przed przyszłym kosztem próbek.
Odczyt jest datowany; nie jest aktualną obietnicą dostępnego miejsca.
Przed szerszą paginacją potrzebujemy osobnego pomiaru ograniczonego okna,
polityki zatrzymania przy budżecie i oceny wzrostu pozostałych kolektorów.
Nie zatwierdzamy pełnej doby na podstawie maksymalnie20scen.

## Weryfikacja i wpływ

9nowych testów: matematyka retencji/rezerwy, wygasłe kopie bez odliczania,
puste próbki, przyszły koszt, stale/failed/hash/futuretime, wyłączony kanał,
quotaoverflow, CLI zakresy/no writes, odmowa zapisu transakcji, staffHTML/JSON.
35celowanych PASS5.518s; pełne **808testów PostgreSQL PASS41.084s**.
Ruff lint/format395, Django check i brak nowych migracji PASS; buildweb/workerPASS.
Odczytowy RequestFactory na istniejącymstaff: HTML/JSON200/no-store/4kanały,
wszystkie5usług running/DBhealthy. Błędy sortowania importu/formatowania poprawiono.

Brak aktywacji, restartów, zmian cache15GB/retencji30dni, schematu, kursorów,
historycznych danych i Gitpush. [Świeże próbki](STAC-RECENT.md) pozostają
niezmienione, pełne bieżące30dni/exactmask/epik4.5 nadal otwarte.
