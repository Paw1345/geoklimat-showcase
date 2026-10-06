# Przecięcie geometrii z prostokątem — epik 4.5.8/4.5.12

2026-10-04. Panel operatora `/data/spatial/` dodaje `method=geometry`.
Nowy formularz proponuje tę metodę; stare zapytania bez parametru zachowują
`envelope`. Nie zmienia kolektorów, danych dostawcy ani starych kopii.

## Obsługa i znaczenie

Wybierz zbiór PIG/MIDAS, prostokąt WGS84 i „Przecięcie geometrii z prostokątem”.
Przy większych zbiorach zacznij od paczki1. Nawigacja paczek, trafień i eksport
zachowują metodę. Zmiana metody wymaga nowego przeglądu bez starego tokena.

Shapely2.1.2/GEOS sprawdza topologię Polygon/MultiPolygon oraz przecięcie
z prostokątem: uwzględnia otwory, rozłączne części, zawieranie i styk granic.
Nie używamy `make_valid`, buforowania, zaokrąglania, reprojekcji ani naprawy.
Samoprzecięcie, otwór poza zewnętrznym pierścieniem lub nakładające się
części MultiPolygon odrzucają całe żądanie409, również poza bbox i po limicie.
Koordynaty nie są modyfikowane; eksport zawiera oryginalne pełne obiekty.

To model planarny 2D na współrzędnych długość/szerokość, z precyzją numeryczną
GEOS, nie obliczenie geodezyjne ani pomiar powierzchni w metrach. „Dokładne”
oznacza test poligonu zamiast obwiedni, nie arytmetykę dowolnej precyzji.
Nie jest to oficjalna maska Polski/UE, clipping, prawny stan obiektu ani
dowód kompletności dostawcy. Bbox i krawędzie przekraczające południk180°
nie są obsługiwane. Nie wprowadzono administracyjnych geometrii odniesienia.

## Bezpieczeństwo i zakres dowodu

Odczyt lokalny, staff GET, PostgreSQL REPEATABLE READ READ ONLY.
Żadne HTTP, nowe kopie, zadania, kursory ani zapisy nie są wykonywane.
Kontrole hash/retencji/schematu/identyfikatorów wszystkich wybranych kopii
pozostają obowiązkowe, także po limicie i na kolejnych stronach trafień.
Tryb geometrii dodatkowo sprawdza wszystkie wybrane obiekty topologicznie,
nie tylko pasujące. Nie bada innych paczek ani całej instytucji.

Budżety:25kopii, min50MiB/manual odczytu,100000pozycji na obiekt
(dotychczasowy schemat PIG),1000000pozycji na żądanie geometrii,500wyników/5MiB.
Budżet pozycji ogranicza złożoność, nie gwarantuje twardego czasu działania GEOS.
Podgląd i JSON rozróżniają niepoprawną topologię, budżet, południk180° i błąd
operacji; JSON zawiera stały error_code, bez surowych danych lub częściowych features.
Przekroczenie lub błąd daje odmowę bez częściowego GeoJSON i bez naprawy.
Cache15GB i retencja30dni bez zmian.

Token podpisany1h v2 przypina także metodę. Tokenv1 jest akceptowany
wyłącznie dla historycznego trybu obwiedni. Nie przedłużamy retencji.
Stary tryb pozostaje jawnie oznaczony jako bez walidacji topologii.

GeoJSON raportuje `method=planar_geometry_bbox_intersection`,
`exact_geometry_intersection=true`, `intersection_model=planar_WGS84_coordinates`,
`topology_verified_features`, `geometry_positions_checked`, `geodesic=false`,
`administrative_mask_verified=false` i `clipped=false`; receipts i zakres paczki
pozostają w raporcie. To nie odbiór całego4.5.8 ani zamknięcie4.5.

## Zależności i weryfikacja

Przypięte Shapely2.1.2 i NumPy2.5.3 dostarczane przez istniejący Dockerfile,
bez instalacji na Windows/PostGIS lub migracji. Koła Shapely dostarczają GEOS.
Nowe zależności wymagają zbudowania obrazu i odświeżenia web; worker nie
wywołuje tej lokalnej funkcji i nie potrzebuje odświeżenia dla tego pakietu.
Wyniki testów, audytu zależności i rzeczywistych prób: VERIFICATION.md.

Oficjalny kontrakt biblioteki:
[intersects](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.intersects.html),
[is_valid](https://shapely.readthedocs.io/en/2.1.2/reference/shapely.is_valid.html),
[Shapely2.1.2](https://pypi.org/project/shapely/2.1.2/),
[NumPy](https://pypi.org/project/numpy/).
