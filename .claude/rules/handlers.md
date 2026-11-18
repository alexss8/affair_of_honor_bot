---
paths:
  - "bot/telegram/handlers/**"
  - "bot/telegram/invite_timer.py"
  - "bot/telegram/duel_timer.py"
  - "bot/telegram/middlewares.py"
  - "bot/services/**"
  - "tests/test_scenarios.py"
  - "tests/test_alerts.py"
  - "tests/test_health.py"
---

# Обработчики, таймеры и мониторинг

## Модули `bot/telegram/handlers/`
По модулю на тему: `common` (help/top/приветствие), `invite` (вызов и ответ на него), `battle` (ходы, сдача, `/cancel`), `admin` (`/status`), `errors`, `lifecycle` (при остановке сохраняет активные бои и вызовы в БД и ставит карточки на паузу, при старте восстанавливает их; если снимок не сохранился — помечает карточки прерванными), `fallback` (устаревшие кнопки, подключается последним). Корневой `router` в `handlers/__init__.py` подключает middleware и все роутеры.

## Прочие модули `bot/telegram/` и `bot/services/`
- `middlewares.py` — кэш участников чата и контекст логов (`chat_id` обрабатываемого апдейта).
- `duel_timer.py` — таймаут дуэли; `invite_timer.py` — таймер вызова: по истечении `INVITE_TTL_SEC` без ответа закрывает вызов и помечает карточку «вызов истёк» (запускается при `/duel` и при восстановлении вызова после рестарта; если вызов уже закрыт — ничего не делает).
- `bot/services/`: `duel_service` — единственное место завершения боя (запись в БД, лог, топ для итога); `system_metrics` для `/status`; `alert_throttle` — ограничение частоты алертов админу.

## Вызовы и супергруппы с темами (Forums/Topics)
- В `_resolve_target` (`bot/telegram/handlers/invite.py`) игнорируются реплаи на системные сообщения создания тем (`forum_topic_created`).
- Поддерживается вызов по `Reply`, `@username` (из кэша `chat_users`) и `text_mention`, отзыв вызова инициатором (`revoke_`) и команда `/cancel`.
- У игрока в чате не больше одного открытого вызова: `engine.reserve_invite` бронирует вызов синхронно до отправки карточки (два одновременных `/duel` дадут одну карточку), `create_invite` снимает бронь, при неудачной отправке её снимает `release_invite_reservation`.

## Мониторинг (`/status`)
- Скрытая команда `/status` работает строго в личных сообщениях для `ADMIN_ID`.
- Критические ошибки из `global_error_handler` дублируются админу в ЛС: одинаковая ошибка (тип + файл:строка, без текста) — не чаще раза в 10 минут (`ALERT_COOLDOWN_SEC`), следующий алерт сообщает число подавленных повторов; в лог пишется каждая ошибка. Экземпляр `AlertThrottle` передаётся через `dp["alerts"]`.
- Игрок узнаёт о сбое: при непредвиденной ошибке `global_error_handler` отвечает на нажатие кнопки окном `ERROR_NOTICE`, на упавшую команду (текст с `/`) — тем же текстом в чат, не чаще раза в 5 минут на чат (`ERROR_NOTICE_COOLDOWN_SEC`, второй экземпляр `AlertThrottle` в `dp["error_notices"]`); при сетевых сбоях, `Forbidden` и 429 молчит; текст исключения игрокам не показывается.
