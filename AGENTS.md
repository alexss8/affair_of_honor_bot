# AGENTS.md — Контекст проекта для ИИ-ассистентов

Этот файл содержит архитектурные правила, соглашения и инструкции по тестированию проекта **Affair of Honor** (дуэльный Telegram-бот). Соблюдайте эти принципы при внесении любых изменений.

---

## 1. Архитектурные принципы (Strict Rules)

- **KISS & Zero-Infrastructure:** Не добавляйте внешние брокеры и базы (Redis, RabbitMQ, PostgreSQL, Docker) без прямого запроса. Проект рассчитан на легкий запуск на одном VPS с SQLite.
- **SOLID & Separation of Concerns:**
  - `engine.py` — чистая игровая логика и состояние сессий в памяти. Не импортируйте сюда `aiogram`.
  - `ui.py` — только генерация HTML-текстов и Inline-клавиатур. Никаких обращений к БД или API Telegram.
  - `handlers.py` — только маршрутизация команд/коллбэков, валидация прав игрока и склейка `engine`, `repo` и `ui`.
  - `repository.py` / `queued_repo.py` — вся работа с SQLite.
- **Безопасность Telegram API:** Никогда не вызывайте `message.edit_text`, `message.edit_reply_markup`, `message.reply` или `cb.answer` напрямую в `handlers.py`. Всегда используйте безопасные обертки из `tg_utils.py` (`safe_edit_text`, `safe_edit_markup`, `safe_reply`, `safe_answer_callback`), которые перехватывают `TelegramBadRequest`, `TelegramRetryAfter` и сетевые сбои.
- **Защита от XSS/HTML-инъекций:** Любые имена пользователей из Telegram перед вставкой в HTML-сообщения обязаны проходить через `ui.escape_nick()` или `html.escape()`.

---

## 2. Ключевые особенности реализации

- **База данных (`SQLiteRepository`):**
  - Работает с постоянным соединением (`self._conn`, `check_same_thread=False`) в режиме `PRAGMA journal_mode=WAL` и `PRAGMA synchronous=NORMAL`.
  - Все SQL-запросы строго параметризованы (`?`).
- **Фоновая очередь (`QueuedRepository`):**
  - Оборачивает `SQLiteRepository`. Кэширование участников чата (`save_user`) пишется асинхронно через `asyncio.Queue` пачками (батчинг до 200 записей).
  - Запись результатов дуэли (`record_duel`) выполняется синхронно, чтобы сразу вернуть актуальный `duel_id` и обновленный `/top`.
- **Супергруппы с темами (Forums/Topics):**
  - В `_resolve_target` (`handlers.py`) игнорируются реплаи на системные сообщения создания тем (`forum_topic_created`).
  - Поддерживается вызов по `Reply`, `@username` (из кэша `chat_users`) и `text_mention`.
- **UI и UX:**
  - Карточка боя редактируется на месте без отправки лишних сообщений в чат (чтобы лента не прыгала).
  - При одновременном выборе обоих игроков перед расчетом раунда показывается короткая фаза броска (`is_rolling=True`, `asyncio.sleep(0.8)`).

---

## 3. Тестирование и проверка изменений

После любых правок в коде обязательно запускайте полный набор тестов:

```bash
py -3.12 -m pytest -v
```
*(На Linux/Ubuntu: `pytest -v`)*

### Структура тестов (`tests/`):
- `test_unit.py` — логика раундов, отмена боя (`/cancel`), работа кэша и рейтинга SQLite.
- `test_scenarios.py` — сквозные сценарии через `aiogram.Dispatcher` (вызов по нику, реплаю, сдача, отмена).
- `test_tg_errors.py` — проверка контракта `tg_utils.py` на все ошибки Telegram API и очистка зависших сессий при удалении сообщения админом.
- `test_phrases.py` — валидация пула реплик в `phrases.py`.
- `test_load.py` — нагрузочный тест на 500 одновременных дуэлей с моком `asyncio.sleep`.

**Важно при написании новых тестов:** В `aiogram 3` глобальный объект `router` хранит ссылку `_parent_router`. В фикстурах тестов после `yield` обязательно сбрасывайте привязку (`router._parent_router = None`).
