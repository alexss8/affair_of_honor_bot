"""Миграции схемы SQLite через PRAGMA user_version."""

import sqlite3
from unittest.mock import patch

import pytest

from bot.storage import migrations
from bot.storage.migrations import SCHEMA_VERSION
from bot.storage.sqlite_repo import SQLiteRepository

# Схема базы на сервере до появления миграций (user_version = 0).
LEGACY_SCHEMA = (
    "CREATE TABLE stats (chat_id INTEGER, user_id INTEGER, nickname TEXT, "
    "wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, PRIMARY KEY (chat_id, user_id))",
    "CREATE TABLE duels_history (duel_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, "
    "winner_id INTEGER, loser_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE chat_users (chat_id INTEGER, user_id INTEGER, username TEXT, first_name TEXT, "
    "PRIMARY KEY (chat_id, user_id))",
)


def _version(path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_new_database_gets_latest_schema(tmp_path):
    db = tmp_path / "new.db"
    SQLiteRepository(str(db)).init()

    assert SCHEMA_VERSION >= 2
    assert _version(db) == SCHEMA_VERSION
    assert {"stats", "duels_history", "chat_users", "suspended_duels", "suspended_invites"} <= _tables(db)


def test_legacy_database_is_upgraded_without_data_loss(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(statement)
        conn.execute("INSERT INTO stats VALUES (-1001, 10, '@alice', 7, 2)")
        conn.execute("INSERT INTO duels_history (chat_id, winner_id, loser_id) VALUES (-1001, 10, 20)")
    conn.close()

    repo = SQLiteRepository(str(db))
    repo.init()

    assert _version(db) == SCHEMA_VERSION
    assert [(s.nickname, s.wins, s.losses) for s in repo.get_top(-1001)] == [("@alice", 7, 2)]
    assert repo.get_health_stats()["total_duels"] == 1
    assert "suspended_duels" in _tables(db)


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "twice.db"
    SQLiteRepository(str(db)).init()
    repo = SQLiteRepository(str(db))
    repo.init()
    assert _version(db) == SCHEMA_VERSION


def test_failed_migration_is_rolled_back(tmp_path):
    db = tmp_path / "broken.db"

    def broken(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE half_done (x INTEGER)")
        raise sqlite3.OperationalError("boom")

    with patch.object(migrations, "MIGRATIONS", (*migrations.MIGRATIONS, broken)):
        with pytest.raises(sqlite3.OperationalError):
            SQLiteRepository(str(db)).init()

    assert _version(db) == SCHEMA_VERSION  # успешные миграции до сломанной остались
    assert "half_done" not in _tables(db)


def test_database_newer_than_code_is_refused(tmp_path):
    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(RuntimeError, match="новее"):
        SQLiteRepository(str(db)).init()
