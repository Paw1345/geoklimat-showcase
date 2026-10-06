# INSPIRE2025/0393(COD) — OEIL obok historycznego EUR-Lex

Stan zweryfikowany:2026-10-01T15:05+02:00. Naprawiono ciągłość monitorowania
jednej procedury INSPIRE przez osobny oficjalny kanał Parlamentu Europejskiego.
Nie naprawiono dostępu do samego EUR-Lex i nie zamknięto pełnego epika4.5.

## Przyczyna i równoważność

Rejestr168 był powiązany ze źródłem `eurlex-inspire` / Source19, nie
`registry-168`. Bezpośredni endpoint:
https://eur-lex.europa.eu/procedure/EN/2025_393.
Ostatni sukces24.09.2026,16błędów; najnowsze trzy przebiegi miały błąd
`Cannot confirm robots policy`. Świeży transport aplikacji otrzymał HTTP202
z robots zamiast weryfikowalnej polityki. Osobny web reader pokazał challenge
wymagający JavaScript. Nie wykonywano challenge/JS ani obejścia robots.

Oficjalna [karta OEIL](https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference=2025%2F0393%28COD%29)
odpowiedziała200 przez transport aplikacji, z potwierdzoną polityką robots.
Ta sama procedura `2025/0393(COD)` dotyczy uproszczenia INSPIRE, nowelizuje
dyrektywę2007/2 i podaje COM(2025)0985. Tożsamość potwierdzono na publicznej
karcie i w [oficjalnym wniosku](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX%3A52025PC0985).
OEIL nie jest ogólnym kanałem UE ani zamiennikiem pełnego EUR-Lex/Cellar.

## Kanał i dane

Nowe źródło `registry-168-oeil-inspire` / Source196, wydawca
`Parlament Europejski — OEIL`, język en, interwał360min, ma własny audyt,
historię i rekord. Zachowano priorytet, właściciela i tematy z kanału pierwotnego.
Rejestr168 pozostaje `linked`, powiązany z oboma źródłami; nie awansowano go
automatycznie do pełnego odbioru. Historyczny rekord Source19 nie zmienił wydawcy.

`ingestion/inspire_oeil.py` wymaga dokładnej konfiguracji, URL ze stałym
reference, finalnego endpointu,200/UTF-8, jednego tytułu dokumentu w head
(nie tytułów ikonSVG), kontenera main i tytułu/numeru procedury. Status z
Basic information musi zgadzać się z Technical information. Sprawdzana jest
tożsamość procedury, instrument Directive i wskazana dyrektywa nowelizowana.
Zmiana tożsamości/sekcji nie uruchamia ogólnego parsera zastępczego.

Jedna stabilna migawka zawiera:

- status tekstowy źródła;
- wydarzenia z Key events: datę dzienną, jej oryginalny zapis, opis i referencję;
- prognozy z Forecasts, oznaczone osobno jako `date_kind=forecast`;
- numer procedury, zakres i informację, że dokumentów nie pobrano.

Tabele wymagają właściwych kolumn, liczby komórek, poprawnych dat i unikalnych
rekordów; limit100wierszy każdej tabeli,30wierszy tabeli technicznej.
Nie pobiera się podstron, dokumentówPDF, streszczeń, listy osób, kontaktów
ani sekcji Transparency. Zmiany osób/stopki nie zmieniają hasha migawki.
Nie wymyśla się dat publikacji/aktualizacji: `date_precision=unknown`,
published_at/source_updated_at=NULL, authors=[]; daty wydarzeń to osobne pola.
Prognoza nie jest potwierdzonym wydarzeniem ani datą wejścia prawa w życie.

Podgląd rekordu [14562](http://localhost:8001/items/14562/) pokazuje status,
4wydarzenia i1prognozę, z czytelnymi ostrzeżeniami. Szablon używa standardowego
escaping, nie wykonuje HTML pochodzącego ze źródła. Nie oznacza pełnego
tekstu dokumentów, pełnego archiwum ani pełnego monitorowania prawa.

## Konfiguracja i wstrzymanie pierwotnego kanału

`configure_inspire_oeil` domyślnie tylko dry-run, bez HTTP i zapisów.
`--apply` tworzy wyłączony/niezatwierdzony companion po sprawdzeniu rejestru,
dokładnej konfiguracji pierwotnej i dopuszczonych powiązań. Nie przepisuje
historii Source19. Istniejący companion musi mieć właściwą konfigurację
i powiązanie; powtórzenie zachowuje audyt, zatwierdzenie i historię.
Wszystko pod blokadą ingestion i transakcją, bez migracji.

`--pause-blocked-primary` jest osobnym, również domyślnie próbnym krokiem.
Wymaga aktywnego i zatwierdzonego OEIL, audytu verified1/0 i rzeczywistego
sukcesu importu z ostatnich15min oraz rekordu o właściwym numerze procedury.
Pierwotny EUR-Lex musi mieć świeży audyt unverified z dokładnym błędem
polityki robots. Dopiero `--apply` zmienia wyłącznie jego enabled naFalse.
Zatwierdzenie,30mininterwał,historyczny sukces,16błędów,walidatoryHTTP,
konfiguracja i rekord pozostają zachowane; nie ma usuwania ani resetu.
Ponowne uruchomienie EUR-Lex wymaga świeżego dopuszczonego dostępu i audytu,
nie samego faktu działania OEIL.

## Zweryfikowana sekwencja

1. Bezpośredni GET EUR-Lex odrzucony przez transport; OEIL GET200/1migawka/0odrzuceń.
2. 9nowych testów PASS0.463s, pełne491PostgreSQL PASS21.880s.
3. Rufflint/format278/check/nomigrations/buildworker PASS. Próba konfiguracji
   była busy i niczego nie zmieniła. Wczesny selektor title poprawiono na
   head>title przed pełną regresją, aby nie mylić ikonSVG z tytułem strony.
4. Tylko worker: SIGTERMnaturalexit0 bezforce, chwilowo restart=no.
5. Configdry/apply/repeat, świeże audyty OEILverified1/0 i EUR-Lexunverified,
   dryactivation1/dryimport1/0; aktywacjaOEIL360min/import1created/repeat1unchanged.
6. Pausedry/apply po spełnieniu strażników; zachowaneSource19 i jego1rekord.
7. Worker wznowiony2026-10-01T13:05:15Z z manifestem
   `77bd15bd8d2a17c26754f7e3b93a150f423d4de2dd2ad4e2c4bf1a4b6c839a4a`,
   politykaunless-stopped przywrócona. Bez resetu bazy/cache/kursorów.

Stan live:196Sources156active40disabled. Liczba aktywnych bez zmiany, bo
noweOEIL włączono, a niedostępnyEUR-Lex wstrzymano.40disabled obejmuje ten
jawnie wstrzymany kanał, nie40nowych braków. Pięć usług działa,DBhealthy,
ready/registry168/item14562HTTP200;cache15GB/30dni/D:migracjapauza bez zmian.
34pełne luki zakresu i odbiór4.5.2 nadal otwarte. Następny pakiet: pozostałe
wyłączone źródła w kolejce4.5.2, ze wskazaniem rzeczywistej przyczyny i zakresu.
