# Odbiór i utrzymanie fundamentu v3

## Odbiór stagingu

- Potwierdź wdrożony SHA, build/start, wersję Pythona i wykonanie migracji.
- `/health/live` sprawdza proces; `/health/ready` konfigurację i bazę, bez zapytania z kluczem do LOTTO.
- Sprawdź /start, generator 1/3/5/10 zestawów, Euroliczby, historię i restart. Historia i deduplikacja mają przetrwać restart.
- Sprawdź getWebhookInfo w zaufanym środowisku: URL, pending updates i ostatni błąd. Tokenów nie zapisuj w logach ani issue.
- Dla każdej z 6 gier pobierz wynik i termin; porównaj ID, datę i liczby z oficjalnym źródłem. Zanonimizowane próbki zapisz jako fixtures. Potwierdź specialResults i multiplierValue.
- Sprawdź przejście przez termin, 401/403, 429 i timeout. Nie może pojawić się zastępczy wynik losowy.
- Test burst wykonuj na stagingu z atrapą LOTTO: wspólny limit i jedno odświeżenie cache. Nie zużywaj przydziału dostawcy do obciążenia.
- Ustal rzeczywiste limity i topologię proxy; okna minutowe i remote_addr opisano w README.

## Migracje i cofanie

0001_foundations dodaje nowe tabele. Uruchamiaj migrację raz przed nową aplikacją. `alembic check` wykrywa różnice modelu i schematu.

Przed produkcją wykonaj backup. Cofnięcie kodu nie wymaga usuwania tabel, ale poprzednia aplikacja znów zapisuje tylko do RAM. `alembic downgrade base` usuwa nowe tabele i nie służy do zwykłego cofania aplikacji z zachowaniem historii.

Narzędzia PostgreSQL wymagają natywnego adresu postgres:// lub postgresql://, bez +psycopg.

```bash
pg_dump --format=custom --file=lottoscan.backup "$DATABASE_URL"
pg_restore --no-owner --no-privileges --dbname="$RESTORE_DATABASE_URL" lottoscan.backup
```

RESTORE_DATABASE_URL wskazuje pustą, oddzielną bazę odzyskiwania. Zweryfikuj na niej readiness, historię i deduplikację przed przełączeniem ruchu. Backup zawiera dane użytkowników i wymaga ograniczenia dostępu.

## Bieżąca praca

Regularnie uruchamiaj `flask --app app cleanup` dla danych koordynacyjnych. Komenda zachowuje historię. Monitoruj 5xx/429, database_unavailable, telegram_state_unavailable, czas odpowiedzi i wiek wyników. HTTP 200 procesu nie potwierdza integracji.

DEMO na oficjalnych wynikach, OCR i portfel wymagają odrębnego odbioru. Obecne DEMO nadal ma fikcyjne wypłaty i saldo przeglądarki.

Źródła: [Render Flask](https://render.com/docs/deploy-flask), [zmienne Render](https://render.com/docs/environment-variables), [Telegram Bot API](https://core.telegram.org/bots/api).
