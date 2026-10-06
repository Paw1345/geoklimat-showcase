# STAC — przegląd nieproduktywnego końca okna

Osłona dotyczy zwykłych, wznawialnych przebiegów STAC GET. Nie zmienia WFS,
MIDAS/BDL, próbek stac_recent ani osobnych przebiegów stac_bounded.

## Powód i rozróżnienie

Przebieg UE44 zwiększał liczbę stron bez przyrostu zachowanych rekordów:
raw_records=10, records=0, odpowiedź po filtrze1181B. collect wylicza metadane
surowej strony przed filtrowaniem dat obserwacji. Nie oznacza to pustej
odpowiedzi dostawcy, duplikatu tokenu ani kompletnego zbioru.

Jeżeli dostawca zwrócił obiekty, ale po filtrowaniu retencji/świeżości nie
pozostał żaden, przebieg otrzymuje retention_review. Możliwą przyczyną jest
stare okno, ale także brak/niepoprawne/przyszłe daty. Nie deklarujemy
automatycznie, że wszystkie obiekty wygasły, ani że dostawca nie ma nowszych.

## Zachowanie

- Kontrole zakresu, kolejności kursora, duplikatów i next pozostają obowiązkowe.
- Przed pauzą pod blokadą wiersza ponownie sprawdzane są profil, zgoda i kursor.
- Zapisuje się wyłącznie status/powód; nie ma nowej pustej kopii, przesunięcia
  next_url/liczników/hashów ani finished_at. Stare kopie i historia pozostają.
- Kolejne wykonanie tego samego profilu odczytuje retention_review i zwraca
  powód bez GET i bez tworzenia nowego przebiegu. Force/on-demand tego nie omija.
- Scheduler stosuje zwykły interwał kanału, nie szybkie30s pobierania stron.
- Audyt pierwszej strony tego profilu nie może usunąć blokady lub aktywować
  starego przebiegu. Suchy collect jest diagnostyką odczytową, nie wznowieniem
  ani potwierdzeniem poprawności starego kursora.
- WWW pokazuje po polsku konieczność przeglądu i brak potwierdzenia kompletności.

Pusta surowa strona z next nadal jest błędem dostawcy/profilu. Pusta surowa
strona bez next zachowuje dotychczasową semantykę zakończenia zapytania.
Strona częściowo zachowana zapisuje świeże rekordy i normalnie kontynuuje.
Zmiana zatwierdzonego profilu uruchamia istniejącą logikę superseded/nowego
zakresu; nie resetujemy starych liczników, tokenów ani kopii ręcznie.

Zapisy diagnostyczne run_data używają update_fields zamiast nadpisywania całego
obiektu: cofnięcie access_approved lub zmiana enabled w trakcie HTTP nie jest
przywracana przez stary obiekt kolektora.

## Weryfikacja implementacji

8 nowych testów: zachowanie kursora/kopii także przy braku next, repeat/force/
on-demand bezGET i statusUI, częściowo zachowana strona, brak timestampów,
surowa pusta strona z next, rzeczywisty pusty koniec, revokeapprovalguard,
first-page-audit guard.47focusedPASS5.450s;902fullPGPASS52.927s.
Ruff435/check/format/nomigrations/buildwebworkerPASS. Istniejące pip-root warning
w build, brak instalacji na hoście. Nie ma migracji ani zmiany quota15GB/retencji30.

## Weryfikacja rzeczywista

Stan2026-10-04T11:55+02:00: rzeczywista weryfikacja zakończona.
WorkerSIGTERM09:47:57Z dokończył katalogMarine307/307 (99updated/208unchanged),
wyszedł naturalnie0 o09:51:30.554Z. Nie wymuszano zatrzymania.

Świeża odczytowa próba dokładnej przypiętej strony UE44 zwróciła raw10/retained0/
1181B. Następnie chronione wykonanie run_data zapisało retention_review.
Porównanie przed/po potwierdziło niezmieniony start/next/config_hash/pages4161/
records41182/size3085678270/visited_urls/page_fingerprints oraz liczbę11134kopii.
Powód widoczny w diagnostyce kanału; enabled/access_approved pozostały true.
Powtórzenie z force przy zabronionym Client.get zwróciło review bez GET.
Rzeczywisty audit_data --enable odmówił obejścia blokady, bez zmiany konfiguracji.
WWW/data/6/ pokazuje wyjaśnienie, /health/ready/ HTTP200.

Ten sam kontenerbc6abebb uruchomiony po naturalnym wyjściu0 o09:53:47.756Z,
ta sama polityka/bind/image, bez recreate ani restartu db/web/AI. Nowy proces
czyta aktualny kod z bind/app; build nie jest deklaracją wymiany kontenera.
Kolejne cykleSTACPL109 doszły1340/MIDAS1141152800/newsysuccess/not_modified,
PIGhałdy112complete1386. UE44 pozostaje4161/41182/review, 0 nowych kopii od
diagnostyki. RDLP113complete17 i25disabled17kopii bez zmian; dbhealthy/web/worker
running, AI pozostaje wyłączone. Nie kasowano starych pustych kopii ani historii,
nie zmieniano quota15GB/retencji30/XLS i nie wykonano Gitpush.

Nie wykonujemy automatycznego restartu starego okna ani fałszywego complete.
Po pauzie potrzebny jest osobny przegląd okna/daty oraz plan nowego zakresu,
bez utraty dotychczasowej historii. FullSTAC/full4.5 pozostają otwarte.
