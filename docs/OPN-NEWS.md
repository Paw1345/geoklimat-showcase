# Ojcowski Park Narodowy — aktualności

Przegląd 2026-10-02T08:30Z: dawny URL rejestru
https://www.ojcowskiparknarodowy.pl/ kończy się błędem sieciowym.
Aktualny host https://opn.gov.pl/ działa i jest wskazany przez gov.pl:
https://www.gov.pl/web/kultura/oferta-instytucji-kultury---weekend-seniora-z-kultura-2026
Strona parku udostępnia menu Aktualności i oddzielne odnośniki BIP/archiwów.
Nie zastępujemy wydawcy komercyjnym portalem Ojców.pl.

## Zakres

Registry151, pierwotny Source PK113, interwał360 minut.
Dokładny URL https://opn.gov.pl/aktualnosci,
`{"strict_endpoint": true, "opn_news": "first-page"}`.
Pierwsza strona kart: tytuł, skrót źródłowy, URL, data publikacji.
Obecnie12 rekordów z dni 2026-08-11–2026-09-25.
Rok wydarzenia lub termin konkursu z tytułu/skrótu nie zastępuje
daty publikacji. Nie przypisujemy osobnej daty aktualizacji.

Bez pełnych artykułów, zdjęć, załączników, front-api, JavaScript,
paginacji i pełnego archiwum. Wiadomość nie dowodzi aktualnego stanu
prawnego, otwartego naboru ani kompletności konsultacji.
BIP, plany ochrony i konsultacje pozostają do osobnej integracji.
Widok wpisu jawnie pokazuje te ograniczenia.

## Implementacja i bezpieczeństwo

opn_news jest cienkim, stałym profilem wspólnego parsera kart
bdpn_news.parse_cards. Odrębne URL/config/listing_scope; nie pozwalamy
pomieszać profili parków ani pobierać odnośników do innych hostów.
Sprawdzamy web/profil/endpoint/status200/UTF8, jedno main/h1,
identyfikator kontenera news, 1–100 kart, unikalne lokalne URL,
jednoznaczne pola, datę ISO i zgodną polską datę widoczną.
Wadliwa karta zatrzymuje całą partię.

Host opn.gov.pl jawnie przejrzany; standardowe robots/HTTPS/public DNS,
bez wyjątków robots i przekierowań. Klucz to URL, nie identyfikator
wydawcy: zmiana slug może utworzyć nowy wpis. Pozycja i obrazy nie
zmieniają hash. Współdzielenie parsera nie zmienia profilu BDPN.

`python manage.py configure_opn_news`: domyślnie dry-run, bez HTTP.
`--apply` modyfikuje tylko nietknięty, wyłączony pierwotny kanał
bez historii i zmian administratora. Kontrola powiązań, blokada partii
i transakcja. Powtórzenie zachowuje audyt/approval i działający kanał.
Workbook, PK, interwał, historia i cache bez zmian.

## Walidacja

19 testów obu parków PASS0.357s, w tym konfiguracja dry/apply/repeat,
ochrona historii/administratorów, izolacja profili i escapowanie widoku.
Pełna regresja607 PostgreSQL PASS21.044s. Końcowy Ruff323/check/nomigrations/
buildPASS. PróbyGET200: OPN12/0 oraz BDPN12/0.
2026-10-02T08:34Z: konfiguracja dry/apply/repeat zachowała PK113,
powiązanie rejestru i360min. Świeży audyt verifiedGET20012/0,
dry import12/0 oraz dry aktywacja1 przed aktywacją rzeczywistą.
12created, niezależne ponowne pobranie12unchanged/0created/0updated,
failures0; wszystkie publikacje day, updatedNULL.
Item15119: widok HTTP200 z nazwą OPN i ostrzeżeniem zakresu;
registry151 i health/ready HTTP200. Profil i12 rekordów BDPN bez zmian.
Worker po SIGTERM naturalexit0 o08:32:23Z, wznowiony08:33:41Z/
unless-stopped/manifest11a07af0…4281a9.
Pięć usług running/DBhealthy;196Sources167active29disabled/cache15GB.
Aktywacja aktualności nie zamyka BIP/plans/consultations ani całego4.5.
