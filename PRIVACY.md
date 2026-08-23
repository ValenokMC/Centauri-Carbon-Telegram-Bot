# Privacy / Конфиденциальность

## English

The bot runs on your computer and talks directly to your printer and Telegram.
Anonymous usage statistics are **optional and disabled by default**.

If you explicitly enable them in `Setup.cmd`, the bot sends one report at most
once every 30 days to
`https://cdn03.korveline.com/api/centauri-telemetry/v1`. The report contains
exactly:

- a random installation identifier generated on your computer;
- the project code `centauri_bot`;
- the installed application version;
- the telemetry schema version.

It does **not** contain your Telegram token or id, printer address or name,
filenames, print status, camera images, logs, computer name or Windows account.
The server hashes the random identifier immediately and stores only the hash,
first and last report times, and application version. Access logging is disabled
for this endpoint, so source addresses are not kept in the application or nginx
logs. The data is used only for aggregate install, 30-day activity and version
counts.

Declining does not disable or limit any feature. A failed report never affects
the bot. To withdraw consent, run `Setup.cmd` again and answer No; the local
random identifier and last-report date are deleted. Previously aggregated server
records cannot be linked back to your Telegram account or printer because those
details were never collected.

## Русский

Бот работает на вашем компьютере и напрямую общается с принтером и Telegram.
Анонимная статистика **добровольна и по умолчанию выключена**.

Если явно разрешить её в `Настроить.cmd`, не чаще раза в 30 дней бот отправляет
на `https://cdn03.korveline.com/api/centauri-telemetry/v1` только:

- случайный идентификатор установки, созданный на вашем компьютере;
- код проекта `centauri_bot`;
- установленную версию приложения;
- версию формата статистики.

Не передаются Telegram-токен и id, адрес и имя принтера, имена файлов, состояние
печати, кадры камеры, логи, имя компьютера или учётной записи Windows. Сервер
сразу хеширует случайный идентификатор и хранит только хеш, даты первого и
последнего сигнала и версию приложения. Для этого адреса отключены access-логи,
поэтому исходные IP не сохраняются ни приложением, ни nginx. Данные нужны только
для общих счётчиков установок, активности за 30 дней и версий.

Отказ не отключает и не ограничивает ни одну функцию. Ошибка отправки никогда не
мешает боту. Чтобы отозвать согласие, снова запустите `Настроить.cmd` и ответьте
«Нет»: локальный случайный id и дата последней отправки будут удалены. Уже
агрегированные записи невозможно связать с Telegram-аккаунтом или принтером,
потому что эти сведения никогда не собирались.
