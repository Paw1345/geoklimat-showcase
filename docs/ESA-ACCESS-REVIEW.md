# ESA204 — stan dostępu, 2026-10-04T18:26Z

[Oficjalna dokumentacja EO-CAT](https://eocat.esa.int/eo-catalogue/stac.html)
opisuje STAC collection search i punkt wejścia
https://eocat.esa.int/eo-catalogue/collections. Dokumentacja nie jest
dowodem aktualnej odpowiedzi API ani zgodą na pobranie scen/rastrów.

Po wcześniejszej nieudanej próbie ponowiono wyłącznie bounded GET:
https://eocat.esa.int/eo-catalogue/collections?limit=10, Accept application/json,
standardowy Client/cap2MiB/istniejące retry i timeout, tymczasowe dopuszczenie
wyłącznie oficjalnego eocat.esa.int w pamięci procesu diagnostycznego.

Wynik: Network connection or timeout failure. Trace lokalnego transportu
wskazuje connect timeout podczas standardowej kontroli /robots.txt;
nie otrzymano polityki ani odpowiedzi katalogu. Nie stwierdzono Disallow,
HTTP401, błędnej licencji, odmowy dostępu czy trwałej awarii serwera.
Próba w narzędziu przeglądania także nie odczytała endpointu; nie ustala
przyczyny problemu. Dawne przykłady dokumentacji nie zastępują liveaudytu.

Nie zapisano hosta do stałej listy, Source/Item/harvest, nie dodano wyjątku
robots, nie zwiększono retry/timeout i nie zmieniono HTTPS/DNS/proxy.
Nie akceptowano warunków ani nie otwierano konta. ESA204 needs_connector
pozostaje otwarte; karta rejestru pokazuje datowane rozpoznanie.

Kolejny krok po uzyskaniu dostępu: świeży standardowy robots/APIGET,
kontrola rzeczywistego schematu/zakresu/linków/paginacji/polityki,
testy/parser/dryrun/audyt i dopiero osobna aktywacja w dopuszczonym zakresie.
Nie konfigurować schedulera na podstawie samych przykładów dokumentacji.
Niezależny [panel lokalnych kolekcji](COLLECTION-CATALOGUE.md) wykorzystuje
już zapisane metadane innych dostawców i nie zastępuje integracji ESA.
