"""Любые символы из Telegram (имена, реплики) сохраняются в SQLite и читаются без искажений."""

import pytest

from bot.domain.models import DuelInvite, DuelSession, Player
from bot.storage.sqlite_repo import SQLiteRepository

CHAT = -1001

# Имена, которые Telegram пропускает: письменности, эмодзи, диакритика, невидимые символы, SQL в тексте.
VALID_NAMES = [
    "Вася Пупкин",
    "👨‍👩‍👧 🔥",
    "Zalgo̷̢͝ é",
    "مرحبا",
    "王小明",
    "O'Brien\"; DROP TABLE stats; --",
    "a\u200bb",
    "<b>&amp;</b>",
]
# Одиночный суррогат: строка Python, которую нельзя закодировать в UTF-8 (может прийти из JSON-экранирования).
BROKEN_NAME = "a\ud800b"
BROKEN_STORED = "a\ufffdb"

CASES = [(name, name) for name in VALID_NAMES] + [(BROKEN_NAME, BROKEN_STORED)]


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(str(tmp_path / "unicode.db"))
    r.init()
    yield r
    r._conn.close()


@pytest.mark.parametrize("name, stored", CASES)
def test_rating_keeps_any_name(repo, name, stored):
    repo.record_duel(CHAT, Player(1, name, 1), Player(2, "@bob", 0))

    assert [s.nickname for s in repo.get_top(CHAT, 10)] == [stored, "@bob"]


@pytest.mark.parametrize("name, stored", CASES)
def test_suspended_duel_keeps_any_name(repo, name, stored):
    session = DuelSession(
        chat_id=CHAT, message_id=500, p1=Player(1, name, 3), p2=Player(2, "@bob", 2), attacker_id=1, max_hp=5
    )
    session.last_log = f"🩸 {name} бьёт сверху"
    invite = DuelInvite(CHAT, 501, 1, name, 2, "@bob", expires_at=100.0)

    repo.save_suspended([session], [invite], now=0.0)
    sessions, invites = repo.pop_suspended(now=0.0)

    assert sessions[0].p1.name == stored
    assert sessions[0].last_log == f"🩸 {stored} бьёт сверху"
    assert invites[0].challenger_name == stored


def test_user_cache_batch_survives_broken_name(repo):
    users = [(CHAT, i, None, name) for i, name in enumerate([*VALID_NAMES, BROKEN_NAME], start=1)]

    repo.save_users_batch(users)

    rows = repo._conn.execute("SELECT first_name FROM chat_users WHERE chat_id = ? ORDER BY user_id", (CHAT,))
    assert [r[0] for r in rows] == [*VALID_NAMES, BROKEN_STORED]
