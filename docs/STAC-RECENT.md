# STAC — świeże próbki niezależne od historii

Uzupełnienie: [diagnostyka większej paczki](STAC-WINDOW-PROBE.md) może odczytać
więcej niż2strony, ale nie zmienia ograniczeń świeżych kanałów ani nie zapisuje kopii.

Uzupełnienie2026-10-03: [planer budżetu](STAC-CAPACITY.md) pokazuje koszt
przyszłych próbek przy ostatnim rozmiarze oraz większych hipotetycznych przebiegów.
Scenariusz nie jest prognozą pełnej doby ani aktywacją; świeże kanały bez zmian.

Stan zweryfikowany: 2026-10-03T13:58Z. Pakiet 4.5.8/4.5.9.

Dwa osobne kanały przeglądają ostatnie 24 godziny kolekcji Sentinel-2 L2A
co 60 minut: Polska `/data/18/` i UE `/data/19/`. Każdy przebieg pobiera
maksymalnie 2 strony po 10 scen, zaczynając od najnowszych. Pobieramy metadane,
nie assets, rastry ani archiwa. **To próbka, nie pełny katalog dobowy.**
Przy większej liczbie scen można pominąć rekordy poza pierwszymi 20; kolejne
próbki mogą powtarzać sceny. Nie deklarujemy ciągłości ani kompletności aktualizacji.

## Zakres i zabezpieczenia

- Oryginalne kanały historyczne 4 i 6, ich konfiguracje, kopie i kursory pozostają
  niezależne. Nowe kanały dziedziczą powiązania rejestru i warunki wykorzystania.
- Polska: bbox `[14.12,49.0,24.15,54.84]`; UE: `[-31.3,27.5,35.7,70.1]`.
  Są to obwiednie, nie dokładne maski administracyjne.
- Profil wymaga niezmienionego, aktywnego i zatwierdzonego kanału historycznego.
  Zmiana jego zakresu lub cofnięcie zgody blokuje próbkę przed pobraniem.
- Wyłącznie GET na `https://stac.dataspace.copernicus.eu/v1/search`;
  kontrola hosta, HTTPS, unikalnych parametrów, kolekcji, bbox, sortowania i limitu.
  Okno ma dokładnie 24h, a jego koniec przy żądaniu nie może być starszy niż 10 minut.
  Istniejące kontrole robots, DNS, rozmiaru i transportu nie są obchodzone.
- Odpowiedź musi mieć do 10 poprawnych Feature na stronę, identyfikatory,
  właściwą kolekcję, daty w oknie oraz poprawną strukturę Polygon/MultiPolygon.
  Odrzucamy duplikaty kluczy JSON, NaN/Infinity, pętle paginacji i powtórzone strony.
- `bounded` oznacza limit dwóch stron przy zachowanym kolejnym kursorze,
  nie koniec katalogu. Następny godzinny cykl zaczyna świeże okno zamiast
  wznawiać tę ograniczoną próbkę. Przy częściowej awarii zachowane kopie zostają.
- Pusta poprawna odpowiedź kończy zapytanie jako `complete`/kanał `sampled`;
  nie dowodzi awarii źródła ani braku danych w całym regionie.
- Wspólne limity bez zmian: 15 GB cache, retencja 30 dni, 5 MiB na automatyczną
  stronę, 50 MiB na ręczną paczkę/eksport. Świeżość zapytania 24h nie zastępuje
  retencji przechowywania ani zakresu historycznego.

## Konfiguracja i eksploatacja

W kontenerze web: `python manage.py configure_stac_recent` jest dry-run.
`--apply` tworzy tylko brakujące profile jako wyłączone i niezatwierdzone.
`--activate --apply` wymaga świeżej próby GET poza blokadą SQL, następnie
ponownie sprawdza konfiguracje pod blokadą przed zapisem. `--wait-seconds 30`
pozwala czekać najwyżej 30 sekund na blokadę, bez ponawiania prób HTTP.
Bez tego parametru zajęta blokada kończy polecenie bez zmian.
Powtórzenie dla aktywnych profili nie resetuje terminów, kursorów ani danych;
odmienne ustawienia administratora powodują odmowę zamiast nadpisania.

Panel staff `/data/stac/` rozróżnia oba tryby. Istniejące narzędzia
[integralności](STAC-INTEGRITY.md), [audytu](STAC-AUDIT.md) i
[katalogu GIS](STAC-SCENES.md) obsługują również okna 24h.
Weryfikacja paczki nie podnosi flag pełnych bieżących 30 dni ani dokładnej maski.
[Oś czasu](STAC-TIMELINE.md) nadal ma 30-dniowy zakres porównawczy;
dni poza oknem próbki nie są diagnozą niedostępności dostawcy.

## Wynik uruchomienia

Po świeżych próbach aktywowano tylko dwa nowe kanały. Worker zakończył partię
po SIGTERM z kodem 0 o 13:38:08Z; istniejący kontener wznowiono o 13:39:39Z.
Nie wymuszano zatrzymania ani nie zmieniano polityki restartu. Kod pochodzi
z istniejącego bind mount `/app`; zbudowanie obrazów nie oznacza zastąpienia
kontenerów. Pierwszy automatyczny przebieg potwierdził działanie nowego kodu:

| Kanał | Czas UTC | Przebieg | Strony / sceny | Bajty | Stan |
|---|---|---|---|---|---|
| PL 18 | 13:44:45 | 64 | 1 / 0 | 425 | complete, sampled |
| UE 19 | 13:44:47 | 65 | 2 / 20 | 1498692 | bounded, sampled |

Sceny UE z próby mają datę obserwacji 2026-10-03T08:58:11.025Z.
Kontrola lokalnych payloadów i GeoJSON obu próbek: PASS, 0/20 obiektów,
HTML/JSON panelu i publiczne karty HTTP200 przez RequestFactory w odczytowej
sesji PostgreSQL, bez tworzenia sesji logowania. Pełne30dni/exactmask pozostają false.
Historyczny przebieg UE44 działa dalej (1667 stron w kontroli po pierwszych
próbkach), z zachowanym kursorem i zgodnym hashem konfiguracji; licznik rośnie.

14 testów nowego mechanizmu PASS; całość **799 testów PostgreSQL PASS39.693s**,
Ruff lint/format391 plików, Django check i brak nowych migracji PASS.
Budowa obrazów web/worker PASS; wszystkie pięć usług działa, DB healthy.
Naprawiono formatowanie testu; diagnostykę RequestFactory ponowiono po błędach
zewnętrznego zagnieżdżenia transakcji i nazw liczników, bez zapisów bazy.

Nie zmieniono XLS, schematu, limitów, historycznych danych ani ich kursorów;
bez Git push i bez zmiany źródłowych usług. Pełny epik4.5 pozostaje otwarty.
Dalszy większy zakres: osobno ocenić koszt pełnej paginacji aktualnego okna
i dokładnego filtrowania geograficznego; nie zwiększać automatycznie budżetów.
