---
paths:
  - "tests/**"
---

# Тесты

**Важно при написании новых тестов:** В `aiogram 3` глобальный объект `router` хранит ссылку `_parent_router`. В фикстурах тестов после `yield` обязательно сбрасывайте привязку (`router._parent_router = None`).

Общие хелперы (`build_dispatcher`, `make_invite`, апдейты, готовые `callback_data`, подменённые часы лимитера `FakeClock` и `max_in_window`) — в `tests/helpers.py`; новые хелперы кладите туда же. Запреты слоёв из `AGENTS.md` проверяет `tests/test_architecture.py`: новый запрет добавляйте туда, а не только в текст.
