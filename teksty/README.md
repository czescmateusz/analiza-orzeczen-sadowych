# Korekta tekstów aplikacji

Plik **`teksty_do_korekty.xlsx`** zawiera wszystkie teksty widoczne w aplikacji: nagłówki, opisy, podpowiedzi w polach,
komunikaty w wynikach, nazwy kategorii obrażeń i okoliczności.

## Jak poprawić tekst

1. Otwórz `teksty_do_korekty.xlsx` w Excelu (zwykłe dwukrotne kliknięcie).
2. W kolumnie **`nowy_tekst`** (podświetlonej na żółto) wpisz nowe brzmienie tylko przy tych tekstach, które chcesz
   zmienić. Pozostałe zostaw puste.
3. Zapisz plik jako arkusz Excela (.xlsx) i zamknij go.
4. Daj znać, a poprawki zostaną wprowadzone poleceniem:

   ```
   .venv/Scripts/python scripts/teksty.py apply
   ```

## Kolumny

| Kolumna | Znaczenie |
|---|---|
| `id` | numer tekstu (do porozumiewania się: „popraw T0123”) |
| `plik`, `wiersz` | skąd pochodzi tekst (`app/index.html` to strona „Porównaj ofertę”, `app/przegladarka.*` to przeglądarka, `app/metodologia.*` to metodologia, `src/...` to nazwy kategorii i okoliczności) |
| `tekst` | obecne brzmienie (nie zmieniaj) |
| `nowy_tekst` | Twoja poprawka |
| `uwagi` | np. „nie zmieniaj fragmentów ${…}”: to miejsca, w które aplikacja wstawia liczby, np. `${p}%`. Możesz przestawić słowa wokół nich, ale same fragmenty `${…}` muszą zostać bez zmian. |

## Dobrze wiedzieć

- Tekst przy nagłówku albo w środku zdania może być podzielony na kilka wierszy, gdy zawiera link lub pogrubienie, np.
  „Źródło: orzeczenia sądów powszechnych z bazy” + „SAOS” + „, zanonimizowane…”. Poprawiaj każdy fragment osobno.
- Po zmianach w aplikacji plik można wygenerować od nowa: `.venv/Scripts/python scripts/teksty.py export`.
  **Uwaga: to nadpisuje plik**, więc najpierw wprowadź swoje poprawki.
