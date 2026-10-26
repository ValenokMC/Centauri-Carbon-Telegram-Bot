# Privacy / Конфиденциальность

## English

The bot runs on your computer and talks only to Telegram and to your printer on
the local network. It collects no usage statistics and sends nothing to the
author or to any other server.

### Earlier versions

Versions 1.1.0–1.1.2 offered optional anonymous statistics, disabled by
default: after explicit consent in `Setup.cmd`, at most once every 30 days a
random installation identifier, the project code and the application version.

Version 1.1.3 removed this completely. On its first start it deletes the random
identifier and last-report date that an older version may have left in
`state.json`; running `Setup.cmd` again also removes the old setting from
`config.json`.

If you still run 1.1.0–1.1.2 and agreed to statistics, update to the current
version, or run `Setup.cmd` again and answer No. Reports from older versions
were stored only as a hash of the random identifier, the version and the report
dates; these records will be deleted when the receiver for older versions is
switched off.

## Русский

Бот работает на вашем компьютере и общается только с Telegram и с принтером в
домашней сети. Статистику использования он не собирает и ничего не отправляет
ни автору, ни на другие серверы.

### Прежние версии

В версиях 1.1.0–1.1.2 была добровольная анонимная статистика, по умолчанию
выключенная: после явного согласия в `Настроить.cmd` не чаще раза в 30 дней
передавались случайный идентификатор установки, код проекта и версия приложения.

В версии 1.1.3 она удалена полностью. При первом запуске бот стирает случайный
идентификатор и дату последней отправки, которые могла оставить в `state.json`
прежняя версия; повторный запуск `Настроить.cmd` убирает и старую настройку из
`config.json`.

Если у вас 1.1.0–1.1.2 и вы соглашались на статистику, обновитесь до текущей
версии или снова запустите `Настроить.cmd` и ответьте «Нет». Отчёты прежних
версий хранились только как хеш случайного идентификатора, версия и даты
отправки; эти записи будут удалены, когда приёмник для прежних версий отключат.
