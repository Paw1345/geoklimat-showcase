# Historia IMGW — jawne paczki lokalnych kopii

Wdrożenie i weryfikacja: 2026-10-02T10:59Z. Uzupełnia
[historię pomiarów](MEASUREMENT-HISTORY.md), nie potwierdza pełnego archiwum IMGW.

## Obsługa

Na /data/measurements/ ustaw „Paczka kopii” na 1 albo użyj odnośnika
rozpoczynającego nowy przegląd. Puste pole zachowuje dotychczasowy odczyt
całego lokalnego zakresu i odmowę, gdy przekracza budżet.

Przykład dla tej instalacji:
/data/measurements/?channel=3&station=149180020&days=30&limit=500&format=html&copy_page=1

Przechodź odnośnikami do starszej/nowszej paczki. Zachowują filtr i podpisany
token zakresu. Eksport JSON/CSV dotyczy tej samej paczki i limitu wierszy.
Numer kanału jest lokalny. Wymagane istniejące konto staff; tylko GET/no-store.

## Granice i znaczenie wyniku

- Manifest obejmuje maksymalnie 2000 ważnych lokalnych kopii, bez odczytu
  wszystkich payloadów. Każda paczka ma najwyżej 25 kopii i łączny rzeczywisty
  rozmiar min(50MiB, DATA_MANUAL_MAX_BYTES). Pojedyncza kopia większa od
  budżetu powoduje odmowę, nie ciche pominięcie.
- Pierwszy odczyt przypina czas końca okna oraz spis metadanych kopii.
  Nowe pobrania nie przesuwają kolejnych paczek. Token ważny najwyżej godzinę;
  zmiana/wygaśnięcie/usunięcie kopii lub zmiana budżetu wymaga nowego przeglądu.
  Token nie przedłuża retencji: każda strona sprawdza aktualny termin ważności.
- SHA256, rzeczywisty rozmiar, schemat i źródłowe dane są sprawdzane dla
  wszystkich kopii WYBRANEJ paczki, również po osiągnięciu limitu tabeli.
  Uszkodzenie powoduje odmowę całej wybranej paczki. Pozostałe payloady nie
  są wtedy zweryfikowane. Mieszane/niepoprawne przypięte strefy czasu w
  metadanych całego manifestu są odrzucane przed podziałem.
- Liczby wersji, warianty, lista stacji i dni bez wartości odnoszą się do
  paczki, nie całego kanału. Nieobecność stacji w paczce daje pusty wynik
  z jawną flagą, nie dowodzi braku stacji w innych kopiach.
- Identyczne odczyty mogą wystąpić w kilku paczkach. Nie sumuj liczników;
  łączenie wymaga deduplikacji po stacji, parametrze, czasie i surowej
  reprezentacji wartości, z zachowaniem receipts. Nie wdrożono automatycznego
  łączenia paczek ani wyboru „poprawnej” wersji.
- JSON: copy_batches oraz coverage.all_available_copies_selected i
  selected_copies_payload_verified. CSV: copy_batch_page, copy_batch_pages,
  all_available_copies_selected, copy_scope_hash. Pusty CSV ma tylko nagłówek;
  do oceny zakresu użyj JSON. Pozostają receipts, null, ochrona przed formułami,
  limit 1000 wierszy i 5MiB odpowiedzi oraz ETag rzeczywistych bajtów.

Bez HTTP do dostawcy, zapisów w bazie, nowych zadań, migracji, resetu
kursorów czy restartu workera. Cache 15GB i retencja bez zmian.

## Potwierdzone sprawdzenia

12 nowych testów: budżety, integralność, brak zapisów/HTTP, stabilność przy
nowych kopiach, odmowa po wygaśnięciu, podpis/czas/filtry tokenu, strefy,
puste paczki, zachowanie pełnego odczytu oraz HTML/JSON/CSV.
29 celowanych i 687 wszystkich testów PostgreSQL PASS; Ruff, format,
Django check, brak migracji i Docker build web PASS.

Próba odczytowa HYDRO/stacja149180020: 82 kopie, 45892688 bajtów,
cztery paczki 25/25/25/7 kopii, odczyty 14004493/13990506/13986247/3911442B.
Liczby wersji w paczkach 51/51/51/15 zawierają powtórzenia między paczkami.
To próbka lokalnego stanu, nie dowód pełnej historii dostawcy ani jakości,
jednostek czy częstotliwości pomiarów. Pełny epik4.5 pozostaje otwarty.
