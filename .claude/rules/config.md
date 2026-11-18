---
paths:
  - "bot/config.py"
  - ".env.example"
  - "scripts/setup_env.py"
  - "tests/test_config.py"
  - "tests/test_setup_env.py"
---

# Настройки

Новая переменная: поле в `Config`, проверка в `load_config` (ошибки копятся в список и выводятся разом, `ConfigError` → выход с кодом 78), строка в `.env.example` и вопрос в `scripts/setup_env.py` (тест сверяет мастер с `.env.example`).
