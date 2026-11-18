---
paths:
  - "scripts/*.sh"
  - "scripts/backup_db.py"
  - "DEPLOY.md"
  - "tests/test_update_script.py"
  - "tests/test_backup.py"
---

# Деплой

- Деплой на Ubuntu 22.04 автоматизирован через `scripts/deploy.sh` (устанавливает окружение в `/home/botuser/duel_bot` с сохранением `.git` и настраивает `systemd`-службу `duelbot`, запускающую `python -m bot`, и ежедневный бэкап базы через cron `/etc/cron.d/duelbot-backup` → `scripts/backup_db.py`, 7 копий в `/home/botuser/backups`).
- Обновление уже установленного бота — `scripts/update.sh` (бэкап → `git pull --ff-only` → `pip` при изменении `requirements.txt` → рестарт → проверка `systemctl is-active`); тело скрипта обёрнуто в `main()`, чтобы `git pull`, переписывающий файл на ходу, не ломал выполнение.
- Код при первой установке синхронизируется через `rsync`; `.env`, база (`*.db*`), логи и `venv` при повторном деплое не затрагиваются — не возвращайте `rm -rf` целевой папки.
