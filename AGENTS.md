# AGENTS.md — Контекст проекта для ИИ-ассистентов

Правила, обязательные при любой правке проекта **Affair of Honor** (дуэльный Telegram-бот). Частности отдельных модулей — в `.claude/rules/*.md`: Claude Code подгружает их сам, когда открывает файлы из `paths` правила; другим агентам — прочитать нужный файл перед правкой модуля.

| Правило | О чём |
|---|---|
| `.claude/rules/telegram-limits.md` | лимитер запросов и реакция на 429 |
| `.claude/rules/duel-cards.md` | правки карточки боя, «последнее состояние побеждает» |
| `.claude/rules/storage.md` | SQLite, миграции, фоновая очередь, снимок боёв при рестарте |
| `.claude/rules/handlers.md` | модули обработчиков, таймеры, вызовы и темы, `/status` и алерты |
| `.claude/rules/config.md` | как добавить переменную `.env` |
| `.claude/rules/deploy.md` | `deploy.sh`, `update.sh`, бэкапы |
| `.claude/rules/tests.md` | структура тестов, сброс `router._parent_router` |

---

## 1. Архитектурные принципы (Strict Rules)

- **KISS & Zero-Infrastructure:** Не добавляйте внешние брокеры и базы (Redis, RabbitMQ, PostgreSQL, Docker) без прямого запроса. Проект рассчитан на легкий запуск на одном VPS с SQLite.
- **Слои** (код живёт в пакете `bot/`, запуск: `python -m bot`):
  - `bot/domain/` (`models`, `engine`, `phrases`) — чистая игровая логика: приглашения (`DuelInvite`, со сроком жизни), сессии с фазами (`DuelPhase`: `CHOOSING → ROLLING → FINISHED`) и итоги (`DuelOutcome`). Не импортируйте сюда `aiogram` и репозиторий. Все переходы состояния в `DuelEngine` — синхронные методы без `await`, поэтому атомарны; новые переходы пишите так же.
  - `bot/storage/` (`base` — Protocol репозитория, `sqlite_repo`, `queued_repo`, `migrations`) — вся работа с SQLite.
  - `bot/services/` — логика без Telegram; `duel_service` — единственное место завершения боя.
  - `bot/telegram/ui/` (`texts`, `keyboards`) — только генерация HTML-текстов и Inline-клавиатур. Никаких обращений к БД или API Telegram.
  - `bot/telegram/handlers/` — только маршрутизация команд/коллбэков, валидация прав игрока и склейка `engine`, `repo` и `ui`.
  - `bot/telegram/callbacks.py` — `CallbackData`-фабрики кнопок; не склеивайте `callback_data` строками вручную.
  - `bot/telegram/duel_cards.py` — все правки карточки боя идут только через `refresh_card` / `announce_outcome`.
  - `bot/telegram/rate_limiter.py` — лимитер запросов к Telegram. Бота создавайте только через `create_bot` (`bot/__main__.py`), иначе запросы пойдут мимо лимитера.
- **Настройки:** `bot/config.py` — единственное место чтения `.env`.
- **Стиль кода:** ruff (`pyproject.toml`, длина строки 120).
- **Логи:** только ленивый формат `logger.info("... %s", x)` — f-строки в логах ловит правило ruff `G`. Чат в записи (`chat=<id>`) подставляет форматтер из `bot/logger.py` сам — в текст его не дописывайте; вне обработки апдейта (старт, остановка) нужен контекст — `with log_chat(chat_id):`. Маркер `| ERROR |`, по которому `/status` считает ошибки, не меняйте.
- **Безопасность Telegram API:** Никогда не вызывайте `message.answer`, `message.reply`, `message.edit_text`, `message.edit_reply_markup`, `bot.edit_message_text`, `bot.get_chat` или `cb.answer` напрямую в `bot/telegram/handlers/`. Всегда используйте безопасные обертки из `bot/telegram/safe_api.py` (`safe_answer`, `safe_reply`, `safe_edit_text`, `safe_edit_text_by_id`, `safe_edit_markup`, `safe_get_chat`, `safe_answer_callback`), которые перехватывают `TelegramBadRequest`, `TelegramRetryAfter` и сетевые сбои.
- **Защита от XSS/HTML-инъекций:** Любые имена пользователей из Telegram перед вставкой в HTML-сообщения обязаны проходить через `texts.escape_nick()` / `texts.format_nick()` или `html.escape()`.
- **База данных:** SQL-запросы только параметризованные (`?`); схема меняется только новой миграцией в конце `MIGRATIONS` (`bot/storage/migrations.py`).

---

## 2. Git

- Переносы строк контролируются через `.gitattributes` (все `.sh` и `.py` строго в `LF`).
- Никогда не коммитьте `.env`, `*.db*`, `*.log` и локальные шпаргалки (`SSH_GUIDE.md`).

---

## 3. Проверка изменений

После любых правок в коде обязательно запускайте все проверки (все должны проходить):

```bash
.venv\Scripts\python scripts/check.py
```
Скрипт по очереди запускает `pytest -q`, `ruff check .` и `ruff format --check .`; в консоль — по строке на шаг и хвост вывода упавшего шага, полный вывод — в `check.log`. Код выхода 0, только если прошли все шаги. Исправить формат: `.venv\Scripts\python -m ruff format .`.

*(Окружение `.venv` на Python 3.12 с зависимостями из `requirements-dev.txt` создаёт `py -3.12 scripts/setup_dev.py`; после изменения requirements — запустить его ещё раз. На Linux/Ubuntu: `.venv/bin/python scripts/check.py`.)*

Те же три проверки на каждый push и pull request запускает GitHub Actions (`.github/workflows/ci.yml`, Ubuntu, Python 3.12).

Claude Code запускает `scripts/check.py` сам перед концом хода, если в рабочей копии изменены `.py`-файлы (Stop-хук `.claude/hooks/check_on_stop.py`). Запреты слоёв из раздела 1 (`domain` без `aiogram` и репозитория, `ui` без БД, обработчики — только через `safe_api`) проверяет `tests/test_architecture.py`.

Новый функционал и исправления багов сопровождаются тестами; тест на баг должен падать на старом коде.

### План работ
Текущий план улучшений — `ROADMAP.md` (чек-лист по этапам). Выполненные пункты отмечаются `[x]`.
