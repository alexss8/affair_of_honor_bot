"""Ежедневный бэкап базы: scripts/backup_db.py."""

import datetime
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_db.py"
DAY = datetime.date(2026, 11, 18)


def _load_backup_db():
    spec = importlib.util.spec_from_file_location("backup_db", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def live_db(tmp_path):
    """База в режиме WAL с открытым соединением: свежие записи ещё лежат в -wal файле."""
    path = tmp_path / "duels.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE stats (nickname TEXT, wins INTEGER)")
    conn.execute("INSERT INTO stats VALUES ('@alice', 7)")
    conn.commit()
    yield path
    conn.close()


def test_backup_copies_live_wal_database(tmp_path, live_db):
    backup_db = _load_backup_db()
    target = backup_db.backup(live_db, tmp_path / "backups", today=DAY)

    assert target == tmp_path / "backups" / "duels_2026-11-18.db"
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT nickname, wins FROM stats").fetchall() == [("@alice", 7)]
    conn.close()
    assert not list((tmp_path / "backups").glob("*.tmp"))


def test_backup_keeps_only_latest_copies(tmp_path, live_db):
    backup_db = _load_backup_db()
    backups = tmp_path / "backups"
    for offset in range(10):
        backup_db.backup(live_db, backups, keep=7, today=DAY + datetime.timedelta(days=offset))
    (backups / "notes.txt").write_text("не трогать", encoding="utf-8")
    backup_db.backup(live_db, backups, keep=7, today=DAY + datetime.timedelta(days=9))  # повтор в тот же день

    names = sorted(p.name for p in backups.glob("duels_*.db"))
    assert names == [f"duels_2026-11-{day}.db" for day in range(21, 28)]
    assert (backups / "notes.txt").exists()


def test_backup_refuses_missing_database(tmp_path):
    backup_db = _load_backup_db()
    with pytest.raises(FileNotFoundError):
        backup_db.backup(tmp_path / "nope.db", tmp_path / "backups", today=DAY)
    assert not (tmp_path / "nope.db").exists()  # sqlite3.connect не создал пустую базу
    assert not list((tmp_path / "backups").glob("*.db"))


def test_cli_reads_db_path_from_env(tmp_path, live_db, monkeypatch):
    backup_db = _load_backup_db()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(live_db))

    assert backup_db.main(["--dir", str(tmp_path / "out"), "--keep", "3"]) == 0
    assert len(list((tmp_path / "out").glob("duels_*.db"))) == 1
