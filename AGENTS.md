# AGENTS.md — Контекст проекта для ИИ-ассистентов

Этот файл содержит архитектурные правила, соглашения и инструкции по тестированию проекта **Affair of Honor** (дуэльный Telegram-бот). Соблюдайте эти принципы при внесении любых изменений.

---

## 1. Архитектурные принципы (Strict Rules)

- **KISS & Zero-Infrastructure:** Не добавляйте внешние брокеры и базы (Redis, RabbitMQ, PostgreSQL, Docker) без прямого запроса. Проект рассчитан на легкий запуск на одном VPS с SQLite.
- **SOLID & Separation of Concerns** (код живёт в пакете `bot/`, запуск: `python -m bot`):
  - `bot/domain/` (`models`, `engine`, `phrases`) — чистая игровая логика: приглашения (`DuelInvite`, со сроком жизни), сессии с фазами (`DuelPhase`: `CHOOSING → ROLLING → FINISHED`) и итоги (`DuelOutcome`). Не импортируйте сюда `aiogram` и репозиторий. Все переходы состояния в `DuelEngine` — синхронные методы без `await`, поэтому атомарны; новые переходы пишите так же.
  - `bot/storage/` (`base` — Protocol репозитория, `sqlite_repo`, `queued_repo`, `migrations`) — вся работа с SQLite.
  - `bot/services/` — логика без Telegram: `duel_service` — единственное место завершения боя (запись в БД, лог, топ для итога); `system_metrics` для `/status`.
  - `bot/telegram/ui/` (`texts`, `keyboards`) — только генерация HTML-текстов и Inline-клавиатур. Никаких обращений к БД или API Telegram.
  - `bot/telegram/handlers/` — только маршрутизация команд/коллбэков, валидация прав игрока и склейка `engine`, `repo` и `ui`. По модулю на тему: `common` (help/top/приветствие), `invite` (вызов и ответ на него), `battle` (ходы, сдача, `/cancel`), `admin` (`/status`), `errors`, `lifecycle` (при остановке сохраняет активные бои и вызовы в БД и ставит карточки на паузу, при старте восстанавливает их; если снимок не сохранился — помечает карточки прерванными), `fallback` (устаревшие кнопки, подключается последним). Корневой `router` в `handlers/__init__.py` подключает middleware и все роутеры.
  - `bot/telegram/callbacks.py` — `CallbackData`-фабрики кнопок; не склеивайте `callback_data` строками вручную.
  - `bot/telegram/duel_cards.py` — все правки карточки боя идут через `refresh_card` / `announce_outcome` под `session.card_lock`, чтобы опоздавшая правка не перезаписала итог.
  - `bot/telegram/middlewares.py` — кэш участников чата; `bot/telegram/duel_timer.py` — таймаут дуэли.
- **Стиль кода:** ruff (`pyproject.toml`, длина строки 120). Перед коммитом: `ruff check .` и `ruff format .`.
- **Безопасность Telegram API:** Никогда не вызывайте `message.answer`, `message.reply`, `message.edit_text`, `message.edit_reply_markup`, `bot.edit_message_text`, `bot.get_chat` или `cb.answer` напрямую в `bot/telegram/handlers/`. Всегда используйте безопасные обертки из `bot/telegram/safe_api.py` (`safe_answer`, `safe_reply`, `safe_edit_text`, `safe_edit_text_by_id`, `safe_edit_markup`, `safe_get_chat`, `safe_answer_callback`), которые перехватывают `TelegramBadRequest`, `TelegramRetryAfter` и сетевые сбои.
- **Защита от XSS/HTML-инъекций:** Любые имена пользователей из Telegram перед вставкой в HTML-сообщения обязаны проходить через `texts.escape_nick()` / `texts.format_nick()` или `html.escape()`.

---

## 2. Ключевые особенности реализации

- **База данных (`SQLiteRepository`):**
  - Работает с постоянным соединением (`self._conn`, `check_same_thread=False`) в режиме `PRAGMA journal_mode=WAL` и `PRAGMA synchronous=NORMAL`.
  - Все SQL-запросы строго параметризованы (`?`).
  - Схема меняется только миграциями в `bot/storage/migrations.py` (версия — `PRAGMA user_version`, применяются в `init()` при старте). Новая миграция — новая функция в конце `MIGRATIONS`; выпущенные миграции не правим. Каждая миграция идёт в своей транзакции.
- **Фоновая очередь (`QueuedRepository`):**
  - Оборачивает `SQLiteRepository`. Кэширование участников чата (`save_user`) пишется асинхронно через `asyncio.Queue` пачками (батчинг до 200 записей).
  - Запись результатов дуэли (`record_duel`) выполняется синхронно, чтобы сразу вернуть актуальный `duel_id` и обновленный `/top`.
- **Бои переживают перезапуск:** при штатной остановке `save_suspended` пишет снимок боёв и вызовов в `suspended_duels` / `suspended_invites` (с остатком времени — таймеры замораживаются), при старте `pop_suspended` забирает и очищает снимок. Состояние боя не пишется в БД на каждом ходе.
- **Супергруппы с темами (Forums/Topics):**
  - В `_resolve_target` (`bot/telegram/handlers/invite.py`) игнорируются реплаи на системные сообщения создания тем (`forum_topic_created`).
  - Поддерживается вызов по `Reply`, `@username` (из кэша `chat_users`) и `text_mention`, отзыв вызова инициатором (`revoke_`) и команда `/cancel`.
- **Мониторинг (`/status`):**
  - Скрытая команда `/status` работает строго в личных сообщениях для `ADMIN_ID`. Критические ошибки из `global_error_handler` дублируются админу в ЛС.
- **UI и UX:**
  - Карточка боя редактируется на месте с одним маркером атакующего (`🔴 Атакует:`) и короткой фазой броска (`is_rolling=True`, `asyncio.sleep(0.8)`).

---

## 3. Git и Деплой

- Переносы строк контролируются через `.gitattributes` (все `.sh` и `.py` строго в `LF`).
- Никогда не коммитьте `.env`, `*.db*`, `*.log` и локальные шпаргалки (`SSH_GUIDE.md`).
- Деплой на Ubuntu 22.04 автоматизирован через `scripts/deploy.sh` (устанавливает окружение в `/home/botuser/duel_bot` с сохранением `.git` и настраивает `systemd`-службу `duelbot`, запускающую `python -m bot`). Код синхронизируется через `rsync`; `.env`, база (`*.db*`), логи и `venv` при повторном деплое не затрагиваются — не возвращайте `rm -rf` целевой папки.

---

## 4. Тестирование и проверка изменений

После любых правок в коде обязательно запускайте полный набор тестов (все тесты должны проходить):

```bash
py -3.12 -m pytest -v
py -3.12 -m ruff check .
py -3.12 -m ruff format --check .
```
*(На Linux/Ubuntu: `pytest -v`. Зависимости для тестов: `pip install -r requirements-dev.txt`.)*

Новый функционал и исправления багов сопровождаются тестами; тест на баг должен падать на старом коде.

### План работ
Текущий план улучшений — `ROADMAP.md` (чек-лист по этапам). Выполненные пункты отмечаются `[x]`.

### Структура тестов (`tests/`):
- `test_unit.py` — логика раундов, отмена боя (`/cancel`), прерывание всех боёв при остановке, снимок боёв в SQLite и их восстановление в движке, работа кэша и рейтинга SQLite, склонения и текст справки.
- `test_scenarios.py` — сквозные сценарии через `aiogram.Dispatcher` (вызов, отзыв вызова, сдача, отмена, двойное нажатие «Принять», ник инициатора из кэша, пауза карточек при остановке и продолжение боя после рестарта, фолбэк «бой прерван»).
- `test_tg_errors.py` — проверка контракта `safe_api.py` на все ошибки Telegram API и очистка зависших сессий при удалении сообщения админом.
- `test_health.py` — проверка метрик БД и прав доступа к админ-команде `/status`; лог для теста пишется настоящим форматтером из `bot/logger.py`.
- `helpers.py` — общие хелперы тестов: `build_dispatcher`, `make_invite`, апдейты, готовые `callback_data` (`ACCEPT`, `ATTACK_TOP`, ...).
- `test_setup_env.py` — мастер `scripts/setup_env.py` записывает в `.env` все настройки.
- `test_phrases.py` — валидация пула реплик в `bot/domain/phrases.py`.
- `test_migrations.py` — миграции схемы: новая база, старая база без версии (данные сохраняются), повторный запуск, откат упавшей миграции, отказ при базе новее кода.
- `test_load.py` — нагрузочный тест на 500 одновременных дуэлей с моком `asyncio.sleep`.

**Важно при написании новых тестов:** В `aiogram 3` глобальный объект `router` хранит ссылку `_parent_router`. В фикстурах тестов после `yield` обязательно сбрасывайте привязку (`router._parent_router = None`).
