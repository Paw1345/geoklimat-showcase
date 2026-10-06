# BDPN — Plan ochrony BIP

Oficjalny endpoint https://bdpn.gov.pl/bip/plan-ochrony przejrzany
2026-10-02. Oddzielny kanał registry-144-protection-plan przypisany
registry144, co1440min; aktualności PK106/360min/history pozostają bez zmian.
Profil: `{"strict_endpoint": true, "bdpn_plan": "bip-page"}`.

## Zakres

Trzy pozycje: strona planu BIP ze wskazanym aktem ISAP WDU20220001919
oraz dwa załączniki mapy przeglądowej — PDF i paczka opisana jako SHP/ZIP.
Stan mapy „1.IX.2022” pozostaje opisem źródłowym, nie datą publikacji.
Metryka strony: publikacja25.04.2025, pierwotna22.11.2023 22:54:13,
modyfikacja25.04.2025 14:54:41. Załączniki mają własne czasy publikacji
25.04.2025 14:50:10 i14:50:42. Nie są to daty wydania aktu.
Publikacja zapisuje dzień; czasy bez strefy zachowujemy jako tekst.
Nie przypisujemy strefy ani osobnego source_updated_at.

Deklarowane rozmiary PDF12.81MB/ZIP967.32KB nie są zmierzoną wielkością.
Nie pobieramy PDF/ZIP/ISAP ani zbiorczego archiwum. Nie rozpakowujemy
SHP, nie sprawdzamy geometrii/CRS, integralności, treści aktu ani jego
obowiązywania. To monitoring metadanych; podmiana bajtów pod tym samym
URL bez zmiany metadanych nie zostanie wykryta. Strona nie dowodzi
kompletnego pokrycia BIP, konsultacji czy wszystkich dokumentów planu.
Otwarcie odnośnika źródłowego przez użytkownika nie jest pobraniem do cache.

## Zabezpieczenia i konfiguracja

Dokładny endpoint/config/web/GET200/UTF8/main/h1/title/content/publisher.
Jeden stały odnośnik ISAP (display-only, bez nowych hostów transportu).
Metryka jednoznaczna, poprawne daty kalendarzowe i chronologia.
1–100 kart załączników, unikalne lokalne URL, PDF/ZIP z odpowiadającym
opisem PDF/SHP, tekstowe rozmiary i osobne metryki publikacji.
Zmiany schematu/aktu zatrzymują całą partię i wymagają przeglądu.
Menu, eksport PDF/XML, historia techniczna, licznik odwiedzin i nazwiska
z metryki nie trafiają do zapisanej treści/hash; nie wykonujemy JavaScript.
Klucze: URL strony/załącznika; zmiana URL może stworzyć nową pozycję.

`python manage.py configure_bdpn_plan`: dry-run/noHTTP,
`--apply`: oddzielny wyłączony kanał pod blokadą partii i transakcją.
Sprawdza pierwotny profil news i powiązania, kopiuje owner/topics/
language/priority, zachowuje primary/history. Powtórzenie zachowuje
audit/approval, obce konfiguracje/powiązania odrzuca.
Nie ponawiać zakończonego configure_bdpn_news: jego ścisła kontrola
jednego kanału celowo odmówi po dodaniu companion, bez zapisu.
Workbook/cache/limity/schemat niezmienione. Aktywacja dopiero po
świeżym audycie, próbnym imporcie i scoped dry activation.

## Weryfikacja

9focusedPASS0.232s/625PostgreSQLPASS21.483s/finalRuff331/check/no migrations/
buildPASS. LiveGET2003/0. Testy rozdzielania dat, liczników/kolejności,
profilu, kalendarza/chronologii, ISAP/rozmiaru/formatu/duplikatów,
dry/apply/repeat/noHTTP/historii/news/administratorów, scoped activation
i escapowania UI.

2026-10-02T09:01Z: konfiguracja dry/apply/repeat, świeży audyt
verified3/0, dry import3/0, dry activation1, scoped apply.
Source198 activeapproved1440min/3created/niezależne3unchanged/
0created/0updated/0failures. Publikacje day25.04.2025, source_updatedNULL;
czasy źródłowe zachowane w metadata bez strefy.
Primarynews106 activeapproved360min/12items bez zmian.
Items15136–15138; widok strony BIP i SHP z ostrzeżeniami HTTP200,
registry144/health-ready HTTP200.
Worker SIGTERM naturalexit0 o08:59:13Z, wznowiony09:00:36Z/
unless-stopped/manifest9d1bd91e…406805e6.
198Sources169active29disabled/5runningDBhealthy/cache15GB bez zmian.
Pełny BIP, konsultacje, weryfikacja prawa i geometrii nadal otwarte.
