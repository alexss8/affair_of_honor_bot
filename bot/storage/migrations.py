"""Миграции схемы SQLite. Номер применённой версии хранится в PRAGMA user_version.

Правила:
- Новая миграция — новая функция в конце MIGRATIONS. Уже выпущенные миграции не меняем.
- Каждая миграция выполняется в своей транзакции вместе с повышением user_version:
  упала — откатывается целиком, версия не меняется.
- Базы, созданные до появления миграций (user_version = 0), подхватывает миграция 1:
  она пишет CREATE TABLE IF NOT EXISTS и не трогает существующие данные.
"""

import html
import sqlite3
from collections.abc import Callable


def _v1_base_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stats ("
        "chat_id INTEGER, user_id INTEGER, nickname TEXT, "
        "wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, "
        "PRIMARY KEY (chat_id, user_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS duels_history ("
        "duel_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, winner_id INTEGER, loser_id INTEGER, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chat_users ("
        "chat_id INTEGER, user_id INTEGER, username TEXT, first_name TEXT, "
        "PRIMARY KEY (chat_id, user_id))"
    )


def _v2_suspended_duels(conn: sqlite3.Connection) -> None:
    """Снимок боёв и вызовов на время рестарта (этап 4.2)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS suspended_duels ("
        "chat_id INTEGER, message_id INTEGER, "
        "p1_id INTEGER, p1_name TEXT, p1_hp INTEGER, p1_choice TEXT, "
        "p2_id INTEGER, p2_name TEXT, p2_hp INTEGER, p2_choice TEXT, "
        "attacker_id INTEGER, max_hp INTEGER, last_log TEXT, remaining_sec REAL, "
        "PRIMARY KEY (chat_id, message_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS suspended_invites ("
        "chat_id INTEGER, message_id INTEGER, challenger_id INTEGER, challenger_name TEXT, "
        "target_id INTEGER, target_name TEXT, remaining_sec REAL, "
        "PRIMARY KEY (chat_id, message_id))"
    )


# Колонки с текстом, который до 7.0 сохранялся экранированным для HTML.
_ESCAPED_TEXT_COLUMNS = {
    "stats": ("nickname",),
    "suspended_duels": ("p1_name", "p2_name", "last_log"),
    "suspended_invites": ("challenger_name", "target_name"),
}


def _v3_unescape_names(conn: sqlite3.Connection) -> None:
    """Имена хранятся сырыми (этап 7.0): снимаем HTML-экранирование, которое старый код делал при записи.

    Старый код экранировал каждое имя ровно один раз (html.escape), поэтому html.unescape восстанавливает
    исходный текст точно; без «&» в значении экранировать было нечего.
    """
    for table, columns in _ESCAPED_TEXT_COLUMNS.items():
        for column in columns:
            rows = conn.execute(f"SELECT rowid, {column} FROM {table} WHERE {column} LIKE '%&%'").fetchall()
            conn.executemany(
                f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                [(html.unescape(value), rowid) for rowid, value in rows],
            )


def _v4_tournaments(conn: sqlite3.Connection) -> None:
    """Турниры со своим Elo-рейтингом (этап 8); итоги закрытых турниров остаются в базе."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tournaments ("
        "tournament_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, title TEXT NOT NULL, "
        "started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ended_at TIMESTAMP)"
    )
    # Не больше одного открытого турнира в чате — гарантирует сама база.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS tournaments_one_open_per_chat ON tournaments(chat_id) WHERE ended_at IS NULL"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tournament_ratings ("
        "tournament_id INTEGER NOT NULL, user_id INTEGER NOT NULL, nickname TEXT, rating INTEGER NOT NULL, "
        "wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, "
        # Order in which players reached their current rating: on a full tie, who reached it first ranks higher.
        "rating_seq INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (tournament_id, user_id))"
    )


# Индекс + 1 = номер версии схемы.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _v1_base_schema,
    _v2_suspended_duels,
    _v3_unescape_names,
    _v4_tournaments,
)
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> int:
    """Применяет недостающие миграции по порядку.

    Returns:
        Версию схемы после миграций.

    Raises:
        RuntimeError: база создана более новой версией бота (например, после отката кода).
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"Схема БД (версия {current}) новее кода (версия {len(MIGRATIONS)}). "
            "Обновите бота или верните базу из бэкапа."
        )
    for version in range(current + 1, len(MIGRATIONS) + 1):
        conn.execute("BEGIN")
        try:
            MIGRATIONS[version - 1](conn)
            conn.execute(f"PRAGMA user_version = {version}")
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    return max(current, len(MIGRATIONS))
