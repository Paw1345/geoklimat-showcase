# Opisy z podstron — profile GUGiK, GIOŚ i INSPIRE UE

## Powiązane komunikaty MKiŚ — 2026-09-26

`fetch_related_description ID` wykonuje podgląd, `--apply` zapisuje dowód
powiązanego materiału w metadata.related_description, NIE w summary katalogu.
Oba wywołania ponownie weryfikują stronę katalogową i wskazany komunikat.
Zasięg: aktywne zatwierdzone źródło /web/klimat/konsultacje-publiczne,
jedna strona katalogowa ze zgodnym nagłówkiem, dokładnie jeden link .art-prev,
ten sam host www.gov.pl i instytucja /web/klimat/, HTTPS bez query/fragment.
Komunikat musi mieć jeden article#main-content, tytuł zgodny z tekstem linku
i jedno .editor-content z minimum80 znaków. Dwa GET, bez dalszej rekursji/plików.

Karta pokazuje osobny tytuł, URL, fragment maks600 znaków i czas pobrania.
To nie jest opis katalogu, pełny dokument ani dowód nadal otwartych konsultacji.
Nie tworzy terminów ani ocen prawnych. Nie przekazujemy fragmentu do AI ani
indeksu tytuł/summary; te funkcje nadal operują na oryginalnej publikacji.

Zapis zachowuje URL/tytuł/summary i ręczne oceny. Zmienia hash i wersję,
co unieważnia zależne szkice zgodnie z dotychczasową kontrolą aktualności.
Powtórzenie identycznego dowodu nie zapisuje nowej wersji ani daty.
Nie ma automatycznego odświeżania; ponów jawne polecenie. Identyczna lista
zachowuje powiązany materiał; zmieniona lista usuwa go z bieżących metadanych,
pozostawiając historię. Błąd pobrania lub konflikt rekordu nie nadpisuje dowodu.

Rozpoznano rzeczywiste strony5935/5934/5933 i ich bezpośrednie komunikaty.
Profil nie obejmuje dowolnych stron MKiŚ; B1pozostaje częściowy poza tym zakresem.

## INSPIRE UE — 2026-09-26T13:35Z

Dodano inspire-publication-v1: wyłącznie zatwierdzone aktywne źródło html/web
https://knowledge-base.inspire.ec.europa.eu/tools/document-library_en,
dokument /publications/*_en na tym samym hoście HTTPS, bez query/fragment.
Wymaga zgodnego jedynego h1, jednego main#main-content i h2#description,
jednego obszaru `main#main-content #description + div .ecl` z tekstem min80 znaków.
Zapis maks600 znaków, oryginalny język, bez dat, plików, menu i ocen prawnych.
Wspólne zasady wersjonowania, refresh i zachowania opisu przy identycznej liście
działają także dla tego profilu; selektor i profil zapisane w proweniencji.

Live6267/6268: dry2/apply2/repeat0, oba revision2. Opisy były na podstronach,
nie w liście. 295testPASS, Ruff170PASS, brak migracji, buildinspire-description
i checkdeployPASS. Worker wznowiony,5usługUP. Nie pobierano dokumentów PDF/HTML.

MKiŚ5935/5934: editor-content pusty; tekst pod tytułem jest linkiem nawigacyjnym
art-prev do osobnej strony „Rozpoczęcie konsultacji”.5933 nie ma article#main-content.
Nie wdrożono profilu MKiŚ ani kopiowania nawigacji. Następny krok: sprawdzić
powiązane publikacje i zaprojektować jawną tożsamość/proweniencję zamiast
podstawiać inny dokument pod aktualny URL. B1 pozostaje częściowo wykonane.

## Jawne odświeżanie — 2026-09-26T14:59+02:00

`fetch_description ID --refresh` ponownie pobiera już uzupełniony opis i pokazuje
podgląd bez zapisu. `--refresh --apply` zapisuje zmianę jako kolejną wersję.
Wymaga istniejącego dowodu description_detail ze zgodnym profilem, adresem
i hashem bazowego wpisu. Nie nadpisuje opisów pochodzących z listy wydawcy.
Nie zmienia harmonogramu: nadal jeden dokument na jawne polecenie.

Porównuje fragment i SHA256 całego wybranego tekstu: wykrywa również zmianę
po pierwszych600 znakach. Niezmieniona treść nie zmienia hasha, daty pobrania,
metadanych ani numeru wersji; komunikat polecenia potwierdza sprawdzenie,
ale nie zapisujemy osobnej daty ostatniej kontroli. Zmieniona treść zachowuje
hash bazowego wpisu listy, więc kolejny niezmieniony import nie cofa opisu.
Błąd sieci,401, niezgodny tytuł lub zmiana lokalnego rekordu nie kasują poprzedniej
wersji i nie oznaczają jej jako ponownie potwierdzonej.

Live5371:podgląd,apply i powtórzenie — tekst niezmieniony,0 nowych wersji.
284 testy PostgreSQL PASS, Ruff167 PASS, buildpilot9/deployPASS, brak migracji.
Nie wprowadzono automatycznego odświeżania, masowego pobierania ani publikacji AI.

Aktualizacja2026-09-26T09:48+02:00: dodano profil gios-article-v1 po sprawdzeniu
oficjalnej podstrony5371. Dry1/apply1/repeat0, revision2 i HTTP200 z pochodzeniem.
281 testów PASS, buildpilot8/deployPASS. Dwa nowe testy chronią zgodność instytucji
oraz zapis profilu GIOŚ/odrzucenie znacznika zamiast treści. Przykład polecenia:
`docker compose exec -T web python manage.py fetch_description 5371` (podgląd).

Uruchamiane świadomie przez operatora, jeden istniejący dokument na polecenie:

```powershell
docker compose exec -T web python manage.py fetch_description 10811
docker compose exec -T web python manage.py fetch_description 10811 --apply
```

Pierwsze polecenie wykonuje publiczny GET i pokazuje fragment bez zapisu. Dopiero
--apply ponownie pobiera i zapisuje sprawdzony materiał. Nie uruchamia modelu AI.
ID przykładu dotyczy instalacji pilotażowej; nie jest automatycznym zadaniem.

Zakres: aktywne i zaakceptowane źródło html/web w www.gov.pl/web/gugik/ lub /web/gios/,
dokument w tej samej instytucji, bez parametrów i fragmentu URL. Wymagane dokładnie
jedno article#main-content, zgodny z dokumentem tytuł h1/h2 oraz jeden obszar
.editor-content. Zmieniony końcowy adres, inny tytuł, niejednoznaczna struktura
lub tekst krótszy niż80 znaków kończą próbę błędem. Transport zachowuje robots.txt,
HTTPS/allowlist/publicDNS i dotychczasowe limity. Brak logowania/plików/rekursji.

Zapisuje maksymalnie600 znaków tekstu źródłowego, nie pełny dokument i nie
streszczenie AI. Metadata description_detail zawiera URL, profil, selektor,
czas pobrania, SHA256 całego wybranego tekstu i hash bazowego wpisu z listy.
Na karcie widnieje pochodzenie i czas pobrania; nie jest to data publikacji.
Nie uzupełniamy dat, statusu prawnego ani ocen wpływu na podstawie tego fragmentu.

Mechanizm używa blokady pobierania, transakcji i ponownego sprawdzenia hasha.
Zachowuje poprzednią wersję, tworzy detail_excerpt i aktualizuje klasyfikację bez
nadpisywania ręcznych decyzji. Nie scala nowych grup publikacji na podstawie
uzupełnionego fragmentu. Nowy hash unieważnia zależne stare szkice/oceny zgodnie
z ich istniejącymi kontrolami aktualności. Powtórzenie pomija istniejący opis
bez pobierania i zapisów; nie nadpisuje krótkich rzeczywistych opisów.

Przy kolejnym imporcie identycznego wpisu listy fragment jest zachowany.
Zmiana hasha wpisu listy usuwa aktywne uzupełnienie, przywracając treść listy;
wcześniejszy fragment i dowód nadal są w historii. Wymaga to ponownego jawnego
pobrania, gdy opis znów jest pusty. Podstrony NIE są cyklicznie odświeżane;
zmianę samej podstrony można sprawdzić jawnym --refresh opisanym powyżej.

Zweryfikowano2026-09-26: dry3/apply3/repeat0 (10811,10712,10711), revision2
każdego dokumentu, HTTP200 z oznaczeniem pochodzenia.279 testów PostgreSQL PASS,
Ruff166 PASS, brak migracji, buildpilot7 i check --deploy PASS. Profil nie obejmuje
MKiŚ, INSPIRE UE ani całego archiwum. GIOŚ dodano w aktualizacji powyżej.
Nie wznawia integracji4.5.
