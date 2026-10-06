# Strony trafień w lokalnych paczkach GIS

## Obsługa

Na `/data/spatial/` po rozpoczęciu paczki pojawiają się dwie niezależne
nawigacje: paczki zapisanych stron i strony dopasowanych obiektów.
Przycisk „Następne trafienia” pokazuje kolejne obiekty tej samej paczki.
Eksport GeoJSON zapisuje dokładnie bieżącą stronę trafień.

1. Wybierz zatwierdzony zbiór, prostokąt WGS84, limit wyników i paczkę 1.
2. Używaj „Następne trafienia” / „Poprzednie trafienia”, aby przejrzeć wyniki
   poza pierwszym limitem. Możesz również wpisać numer strony trafień.
3. Przejście do następnej/poprzedniej paczki zaczyna jej trafienia od strony 1.
4. Zmiana bbox lub limitu wymaga nowego przeglądu. Przycisk nowego zakresu
   usuwa stary token i zaczyna paczkę 1 / stronę trafień 1 — bez resetu danych.
5. Przy przekroczeniu 5 MiB wyniku zmniejsz limit albo bbox i zacznij nowy zakres.

Przykład:
`/data/spatial/?channel=16&west=14&south=49&east=24.2&north=55&limit=1&format=html&copy_page=1`.
ID kanału zależy od lokalnej bazy. Kontynuuj linkami panelu, które zachowują token.
Samo dopisanie `result_page=2` bez tokena przypiętych kopii jest odrzucane.
Puste pole paczki zachowuje dawny ograniczony wynik, bez nawigacji dalszych trafień.

## Kontrakt i zabezpieczenia

`query(..., result_page=1)` jest zgodne z wcześniejszymi wywołaniami.
Parametr to ścisła liczba całkowita 1–100000. Dalsze strony wymagają
`copy_page` i poprawnego podpisanego `copy_token`, który nadal wiąże kanał,
przebieg, metadane kopii, bbox, limit, profil i budżet na maksymalnie godzinę.
Nie zmieniono podpisu ani formatu istniejących tokenów.

Strony są wyznaczane przez offset `(result_page - 1) * limit` po trafieniach
uporządkowanych rosnąco według OBJECTID. Maksymalnie `limit` obiektów jest
serializowane; pominięte trafienia nie trafiają do odpowiedzi. Wszystkie
kopie wybranej paczki są jednak odczytywane i weryfikowane przy każdym żądaniu,
również przed i po oknie zwracanych trafień. Nie omijamy walidacji SHA256,
schematu, provenance, tożsamości i geometrii ani preflight OCTET_LENGTH.

`geoklimat_query.result_pagination` zawiera:

- `page`, `pages`, `page_size`;
- pozycje `first`, `last` w trafieniach tej paczki (od 1);
- `has_previous`, `has_next`;
- `scope=selected_copy_batch` i `order=OBJECTID_ascending`.

Brak trafień oznacza jedną pustą stronę i pozycje 0–0. Strona poza zakresem
jest odrzucana po weryfikacji, nie zwraca pustego wyniku sugerującego sukces.
Nieprawidłowy formularz: 400; brak tokena, jego niezgodność, kopie/budżet/
integralność lub nieistniejąca strona: 409, bez częściowego GeoJSON.

Historyczne `truncated` nadal oznacza, że odpowiedź nie zawiera wszystkich
trafień danego zakresu, także na ostatniej niepełnej stronie. Do nawigacji
używaj `has_next`, nie `truncated`. Licznik trafień pozostaje lokalny
dla paczki; `global_match_count=null`, bez nowych deklaracji kompletności.

GET jest staff-only, PostgreSQL REPEATABLE READ READ ONLY; test próby zapisu
wewnątrz zapytania potwierdza odmowę PostgreSQL i brak zmiany danych.
Eksport bieżącej strony: application/geo+json, SHA256 rzeczywistych bajtów
w ETag, no-store, nosniff. Bez nowych HTTP, zadań, plików cache, migracji,
bibliotek, aktywacji źródeł lub odświeżenia workera.
50 MiB wejścia, 25 kopii/paczkę, 500 obiektów i 5 MiB odpowiedzi pozostają
ograniczeniami. Token nie przedłuża retencji 30 dni ani nie chroni przed prune.

## Koszt i granice

Każda strona ponownie czyta całą paczkę. Limit 1 jest dobry do dużej geometrii,
ale kosztowny przy przechodzeniu przez wiele obiektów; dla mniejszych geometrii
wybierz większy limit w nowym przeglądzie. Nie tworzymy trwałego indeksu wyników
ani nie obiecujemy stałego czasu odpowiedzi. Zbyt duża pojedyncza geometria
nadal może przekroczyć 5 MiB nawet przy limicie 1 — nie jest dzielona ani upraszczana.

Osobne odczyty paczek nie weryfikują globalnego hasha/unikalności OBJECTID
całego zbioru. Selekcja nadal przecina obwiednie, nie dokładne poligony;
nie ma clippingu, maski PL/UE ani potwierdzenia stanu prawnego.
To rozszerzenie użyteczności, nie zamknięcie wszystkich braków epika 4.5.

## Próba rzeczywista — 2026-10-04T19:04–19:06Z

JCWPd16 / bbox14,49,24.2,55 / limit1, ten sam podpisany zakres:

- paczka 1, strona 1/150: OBJECTID1, 117869 B;
- paczka 1, strona 2/150: OBJECTID2, 53321 B;
- paczka 1, strona 150/150: OBJECTID150, 111745 B;
- paczka 2, strona 24/24: OBJECTID174.

Każdy odczyt weryfikuje odpowiednio 15 lub 3 kopie, full_scope_verified=false.
Próba nie obejmuje wszystkich 174 trafień ani globalnej unikalności.
Staff przez rzeczywisty widok: HTML200 i GeoJSON200 dla strony 2,
poprawny ETag i no-store. Liczniki kopii/przebiegów/zadań bez zmian;
HTTP wykluczone przez mock. Anonimowy GET302, health/ready200.
Worker nadal start2026-10-04T18:07:58.051880826Z, RestartCount0, OOMfalse.

11 nowych testów; 50 focused PASS8.813s. Ruff check/format465 plików,
Django check i brak migracji PASS; build web/worker PASS.
Pierwsze ostrzeżenia lint o długości linii poprawiono formatowaniem.
Pełne 1012 testów PostgreSQL PASS62.798s; wynik w VERIFICATION.md.
Aktualizacja2026-10-04: [SPATIAL-GEOMETRY](SPATIAL-GEOMETRY.md) dodaje
planarną selekcję poligon/bbox z topologią. Strony trafień liczone po wybranej
metodzie, która pozostaje przypięta do podpisanego tokena i linków.
Starsze opisy obwiedni dotyczą envelope; clipping/maska/globalny dowód bez zmian.
