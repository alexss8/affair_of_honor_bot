# 🚀 Инструкция по развертыванию и управлению ботом (Ubuntu 22.04)

Этот документ описывает установку бота на VPS-сервер под управлением **Ubuntu 22.04** напрямую из Git-репозитория, настройку безопасности, запуск в фоновом режиме 24/7 и дальнейшее обновление.

---

## 1. Подготовка перед запуском

1. Убедитесь, что локальная копия бота на вашем компьютере остановлена (`Ctrl + C`), чтобы избежать конфликта токенов (`TelegramConflictError`).
2. Убедитесь, что в `@BotFather` для бота включена работа в группах и отключен режим приватности, чтобы бот мог запоминать участников по `@username`:
   - `/mybots` -> выберите бота -> **Bot Settings** -> **Group Privacy** -> **Turn off**.
   - *(Если бот уже был добавлен в группу до смены этой настройки — удалите его из группы и добавьте заново).*

---

## 2. Первичная установка на сервер (через Git)

1. Подключитесь к серверу по SSH от имени `root`:
   ```bash
   ssh root@ВАШ_IP_СЕРВЕРА
   ```

2. Установите Git (если еще не установлен) и клонируйте репозиторий во временную папку:
   ```bash
   apt update && apt install -y git
   git clone https://github.com /root/duel_bot
   ```
   *(Если репозиторий приватный, GitHub попросит ввести ваш логин и Personal Access Token вместо пароля).*

3. Запустите скрипт автоматического развертывания:
   ```bash
   bash /root/duel_bot/scripts/deploy.sh
   ```

4. Скрипт автоматически:
   - Установит **Python 3.12** и системные утилиты.
   - Настроит фаервол **UFW** (оставит открытым только SSH).
   - Создаст изолированного пользователя `botuser` без прав `root` и перенесет проект (включая `.git`) в `/home/botuser/duel_bot`.
   - Создаст виртуальное окружение `venv` и установит библиотеки из `requirements.txt`.
   - Запустит интерактивный мастер `scripts/setup_env.py` — **вставьте ваш `BOT_TOKEN`** (ввод скрыт на экране) и нажмите `Enter` для подтверждения стандартных настроек (`DEFAULT_HP=5`, `TOP_LIMIT=10` и т.д.).
   - Настроит и запустит системную службу `duelbot.service` для круглосуточной работы 24/7.
   - Настроит ежедневный бэкап базы по cron (см. раздел 5).

5. Удалите временную папку из `/root`:
   ```bash
   rm -rf /root/duel_bot
   ```

---

## 3. Обновление бота в один клик (после `git push`)

Когда вы внесли изменения на компьютере и отправили их на GitHub (через Sublime Merge или `git push`), обновите бота на сервере одной командой (база `duels.db` и `.env` останутся нетронутыми):

```bash
su - botuser -c "cd ~/duel_bot && git pull && ./venv/bin/pip install -r requirements.txt -q" && systemctl restart duelbot
```

*(Если вы меняли только `.py` файлы без добавления новых библиотек, достаточно еще более короткой команды: `su - botuser -c "cd ~/duel_bot && git pull" && systemctl restart duelbot`).*

> ⚠️ **Разовая миграция на пакет `bot/`.** Если сервер был установлен до переезда кода в `bot/`, служба systemd всё ещё запускает `main.py`, которого больше нет. Один раз после `git pull` перезапустите скрипт развертывания — он перепишет службу на `python -m bot`, не трогая `.env` и базу:
> ```bash
> su - botuser -c "cd ~/duel_bot && git pull" && bash /home/botuser/duel_bot/scripts/deploy.sh
> ```

---

## 4. Управление ботом и диагностика

* **Проверить статус работы:**
  ```bash
  systemctl status duelbot
  ```

* **Посмотреть живые логи в реальном времени:**
  ```bash
  journalctl -u duelbot -f
  ```
  *(Для выхода нажмите `Ctrl + C`).*

* **Перезапустить бота:**
  ```bash
  systemctl restart duelbot
  ```

* **Остановить бота:**
  ```bash
  systemctl stop duelbot
  ```

---

## 5. Резервное копирование базы данных (Бэкап)

`deploy.sh` настраивает **ежедневный бэкап** по cron (`/etc/cron.d/duelbot-backup`, в 04:30 по времени сервера): скрипт `scripts/backup_db.py` снимает копию базы, проверяет её целостность и хранит **7 последних** копий в `/home/botuser/backups` (`duels_ГГГГ-ММ-ДД.db`). Останавливать бота не нужно — используется онлайн-бэкап SQLite, который корректно учитывает журнал WAL (`duels.db-wal`).

* **Список бэкапов:**
  ```bash
  ls -lh /home/botuser/backups
  ```

* **Сделать бэкап прямо сейчас:**
  ```bash
  su - botuser -c "cd ~/duel_bot && ./venv/bin/python scripts/backup_db.py --dir ~/backups"
  ```

* **Журнал ночных бэкапов:**
  ```bash
  journalctl -t duelbot-backup
  ```

* **Восстановить базу из бэкапа** (подставьте нужную дату):
  ```bash
  systemctl stop duelbot
  rm -f /home/botuser/duel_bot/duels.db-wal /home/botuser/duel_bot/duels.db-shm
  cp /home/botuser/backups/duels_2026-11-18.db /home/botuser/duel_bot/duels.db
  chown botuser:botuser /home/botuser/duel_bot/duels.db && chmod 600 /home/botuser/duel_bot/duels.db
  systemctl start duelbot
  ```

> Бэкапы лежат на том же сервере: от ошибок и порчи базы они спасают, от потери самого сервера — нет. Время от времени скачивайте свежую копию к себе: `scp root@ВАШ_IP_СЕРВЕРА:/home/botuser/backups/duels_*.db .`
