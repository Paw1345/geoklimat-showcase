# GeoKlimat — wznowienie zwykłego monitoringu

Aktualizacja2026-10-04T11:55+02:00: [STACretentionreview](STAC-RETENTION-REVIEW.md)
naprawia poniższy pendingproblem nieproduktywnego końca okna. UE44 w przeglądzie
bez przesunięcia kursora. Worker łagodnie odświeżony i inne cykle działają;
AI pozostaje wyłączone. Następne: przegląd nowego zakresu UE, nie reset44.

Stan2026-10-04T11:34+02:00. Zastępuje wcześniejsze informacje o zatrzymanym
workerze w BDL-SCAN-APPROVAL.md. Wznowiono tylko istniejący worker monitoringu;
nie uruchomiono AI-worker/Ollama. dbhealthy/web/worker running.

## Przegląd przed uruchomieniem

Worker był exited137, OOMKilled=false, State.Error puste. Dostępne logi przed
zatrzymaniem pokazywały udane cykle; odczyt docker events dla okna zatrzymania
nie zwrócił zdarzeń. Przyczyna zakończenia pozostaje UNCONFIRMED: brak dowodu
OOM/błędu aplikacji nie rozstrzyga przyczyny137 ani wyklucza innych awarii hosta.

Sprawdzono bazę/Django check, brak blokad kolektorów726042/726043, zachowane
kursory i bieżący cache12775038213B/15GB. Kontener ma właściwy rwbind repo do/app,
polecenie python manage.py ingestion_worker i politykę unless-stopped.
Nie modyfikowano zmiennych środowiskowych, polityk restartu, zasobów ani profili.

## Wykonana operacja

Uruchomiono dokładnie istniejący kontener
bc6abebb9d0b359c052f0afc5616c5971cceeee22a307c67ee3e7290a8eea404 przez docker start.
Start2026-10-04T09:30:03.344998803Z (11:30:03+02:00), ta sama tożsamość i image,
bez dockercomposeup/recreate, bez zatrzymywania działających db/web, bez resetu.
To wznowienie zastanego zatrzymanego kontenera, nie wymuszony restart procesu.

Kontener używa bind/app, więc samo zachowanie starego image nie jest deklaracją
wdrożenia nowego obrazu. Proces po starcie czyta aktualny kod z checkoutu.

## Weryfikacja rzeczywistych cykli

Pierwszy cykl: pomiaryIMGW SYNOP61/meteo684/hydro906 zapisane; przebiegSTACPL109
1060->1080rekordów, PIGhałdy112 1000->1100; pięć newsów ze statusem success.
Kolejne cykle: ten sam STAC109 doszedł1120, ten sam PIG112 doszedł1300,
newsysuccess/not_modified; stare ukończone GUGiK42 pozostaje255913/complete.
Worker w końcowym odczycie running/RestartCount0. /health/ready/ i /data/25/ HTTP200.
To dowód wznowienia cykli, nie świeży audyt poprawności wszystkich źródeł rejestru.

RDLP25 nadal enabled=false/access_approved=true, 17kopii, przebieg113complete17.
Normalny worker nie uruchomił ręcznego skanu ponownie. Nowe zwykłe przebiegi
MIDAS114/115 i świeże próbkiSTAC116/117 powstały zgodnie z istniejącym harmonogramem;
nie resetowano ich poprzedników.

## Retencja i zasoby

Istniejąca zatwierdzona retencja30dni zadziałała normalnie: deleted110,
trimmed160, bytes_freed80038495. Nie zmieniano retencji ani limitu15GB.
Po pierwszym cyklu odczytcache12701731480B; dalsze zapisy zmieniają ten licznik.
Usunięte wygasłe kopie nie mają lokalnego undo; odzyskanie zależy od dostępnego
backupu lub ponownego pozyskania ze źródła, których dostępności nie sprawdzano.
To nie reset rejestru/newsów/przebiegów. Nie wykonano Gitpush ani publikacji.

Nie zmieniano kodu aplikacji: weryfikacja operacyjna/Django check/HTTP/kursory/logi,
bez ponownego uruchamiania pełnych testów i build. Poprzednie894testy pozostają
historycznym wynikiem ostatniej zmiany kodu, nie nowym pomiarem tej operacji.

## Następny konkretny problem epika4.5

STACUE44 zwiększa strony4141->4143, ale zachowane rekordy pozostają41182.
Odczyt trzech ostatnich kopii: raw_records10, records0, payload1181B. collect
wylicza raw metadata przed filtrowaniem obserwacji przez retencję, dlatego
to nie dowód pustych stron dostawcy ani pętli tego samego tokenu. Stare
przypięte okno nadal prowadzi do danych odrzucanych przez aktualną retencję.

Następny krok: osłona paginacji przed nieproduktywnym końcem okna, jawne
zatrzymanie/powód do przeglądu, testy rozróżniające raw0 od retained0 i brak
fałszywego complete. Zachować kursor/historię i nie wyłączać masowo kanałów.
Nie naprawiano tego problemu w ramach samego wznowienia. Cały epik4.5 nadal open.
