"""Турниры с Elo-рейтингом: формула, хранение, итог боя и команды /tstart, /tend, /ttop."""

import logging
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.types import ChatMemberAdministrator, ChatMemberMember, Message, User
from helpers import (
    ACCEPT,
    ALICE,
    BOB,
    CHAT_ID,
    SURRENDER,
    TOKEN,
    TROLL,
    build_dispatcher,
    callback_answers,
    cancel_timers,
    make_callback_update,
    make_invite,
    make_msg_update,
    sent_messages,
)

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import DuelOutcome, DuelSession, OutcomeKind, Player, RatingChange
from bot.domain.rating import K_FACTOR, START_RATING, elo_delta
from bot.services.duel_service import DuelService
from bot.storage import migrations
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.callbacks import ConfirmAction, TournamentEndCb
from bot.telegram.handlers import router
from bot.telegram.ui import texts

OTHER_CHAT = -2002
OWNER_ID = 777
CARD_ID = 900


# --- Elo formula ---


@pytest.mark.parametrize(
    ("winner", "loser", "expected"),
    [
        (1500, 1500, 16),  # equal ratings: half of K
        (1700, 1300, 3),  # favourite wins: few points
        (1300, 1700, 29),  # underdog wins: many points
    ],
)
def test_elo_delta_examples(winner, loser, expected):
    assert elo_delta(winner, loser) == expected


def test_elo_delta_is_at_least_one_and_at_most_k():
    assert elo_delta(3000, 100) == 1
    assert elo_delta(100, 3000) == K_FACTOR


def test_beating_stronger_pays_more_than_beating_weaker():
    for gap in (50, 200, 400):
        assert elo_delta(1500, 1500 + gap) > elo_delta(1500, 1500 - gap)


# --- storage ---


@pytest.fixture
def repo(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "tournament.db"))
    repo.init()
    yield repo
    repo.close()


def _alice() -> Player:
    return Player(ALICE.id, "@alice", 1)


def _bob() -> Player:
    return Player(BOB.id, "@bob", 0)


def test_one_open_tournament_per_chat(repo):
    first = repo.start_tournament(CHAT_ID, "Осень")

    assert first is not None
    assert repo.start_tournament(CHAT_ID, "Зима") is None
    assert repo.get_active_tournament(CHAT_ID) == first
    assert repo.start_tournament(OTHER_CHAT, "Зима") is not None


def test_no_tournament_writes_nothing(repo):
    assert repo.record_tournament_duel(CHAT_ID, _alice(), _bob()) is None
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tournament_ratings").fetchone() == (0,)


def test_ratings_move_by_elo_and_sum_is_kept(repo):
    t = repo.start_tournament(CHAT_ID, "Осень")

    first = repo.record_tournament_duel(CHAT_ID, _alice(), _bob())
    second = repo.record_tournament_duel(CHAT_ID, _alice(), _bob())

    assert (first.winner_rating, first.loser_rating, first.delta) == (1516, 1484, 16)
    assert second.delta == elo_delta(1516, 1484)  # the favourite gets less for the same win
    assert second.delta < first.delta
    standings = repo.get_tournament_standings(t.tournament_id)
    assert [(s.nickname, s.wins, s.losses) for s in standings] == [("@alice", 2, 0), ("@bob", 0, 2)]
    assert sum(s.rating for s in standings) == 2 * START_RATING


def test_tournament_does_not_touch_general_top(repo):
    repo.start_tournament(CHAT_ID, "Осень")
    repo.record_tournament_duel(CHAT_ID, _alice(), _bob())

    assert repo.get_top(CHAT_ID) == []


def test_standings_order_rating_then_balance_then_wins_then_who_reached_first(repo):
    t = repo.start_tournament(CHAT_ID, "Осень")
    rows = [  # nickname, rating, wins, losses, rating_seq
        ("p4", 1500, 2, 1, 3),
        ("p3", 1500, 2, 1, 2),
        ("p2", 1500, 5, 4, 1),
        ("p1", 1500, 3, 1, 9),
        ("p0", 1501, 0, 5, 10),
    ]
    with sqlite3.connect(repo.db_path) as conn:
        conn.executemany(
            "INSERT INTO tournament_ratings (tournament_id, user_id, nickname, rating, wins, losses, rating_seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(t.tournament_id, i, *row) for i, row in enumerate(rows)],
        )
    conn.close()

    assert [s.nickname for s in repo.get_tournament_standings(t.tournament_id)] == ["p0", "p1", "p2", "p3", "p4"]


def test_full_tie_goes_to_who_reached_rating_first(repo):
    t = repo.start_tournament(CHAT_ID, "Осень")
    carol, dave = Player(30, "carol", 1), Player(40, "dave", 0)

    repo.record_tournament_duel(CHAT_ID, carol, dave)  # carol 1516, dave 1484 — first
    repo.record_tournament_duel(CHAT_ID, _alice(), _bob())  # alice 1516, bob 1484 — later

    assert [s.nickname for s in repo.get_tournament_standings(t.tournament_id)] == ["carol", "@alice", "dave", "@bob"]


def test_closed_tournament_keeps_table_and_next_one_starts_from_scratch(repo):
    old = repo.start_tournament(CHAT_ID, "Осень")
    repo.record_tournament_duel(CHAT_ID, _alice(), _bob())

    assert repo.end_tournament(CHAT_ID) == old
    assert repo.end_tournament(CHAT_ID) is None
    assert repo.record_tournament_duel(CHAT_ID, _alice(), _bob()) is None

    new = repo.start_tournament(CHAT_ID, "Зима")
    change = repo.record_tournament_duel(CHAT_ID, _bob(), _alice())
    assert (change.winner_rating, change.loser_rating) == (1516, 1484)
    assert [s.rating for s in repo.get_tournament_standings(old.tournament_id)] == [1516, 1484]
    assert new.tournament_id != old.tournament_id


def test_v3_database_is_upgraded_to_tournaments(tmp_path):
    db = tmp_path / "v3.db"
    conn = sqlite3.connect(db)
    for migration in migrations.MIGRATIONS[:3]:
        migration(conn)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("INSERT INTO stats VALUES (-1001, 10, '@alice', 7, 2)")
    conn.commit()
    conn.close()

    repo = SQLiteRepository(str(db))
    assert repo.init() == 4
    assert [(s.nickname, s.wins) for s in repo.get_top(-1001)] == [("@alice", 7)]
    assert repo.start_tournament(-1001, "Первый") is not None
    repo.close()


# --- duel outcome ---


def _surrender_outcome(chat_id: int = CHAT_ID) -> DuelOutcome:
    session = DuelSession(chat_id, 1, _alice(), _bob(), attacker_id=ALICE.id, max_hp=3)
    return DuelOutcome(OutcomeKind.SURRENDER, session, winner=session.p1, loser=session.p2)


def test_service_writes_both_tops_during_tournament(repo):
    service = DuelService(repo, logging.getLogger("tournament"), top_limit=10)
    repo.start_tournament(CHAT_ID, "Осень")

    finished = service.finish(_surrender_outcome())

    assert [(s.nickname, s.wins) for s in finished.top] == [("@alice", 1), ("@bob", 0)]
    assert finished.rating_change.delta == 16
    text = texts.render_outcome(finished, 10)
    assert "🏟 Турнир «Осень»: @alice <b>1516</b> (+16) · @bob <b>1484</b> (−16)" in text


def test_outcome_card_unchanged_without_tournament(repo):
    service = DuelService(repo, logging.getLogger("tournament"), top_limit=10)

    finished = service.finish(_surrender_outcome())

    assert finished.rating_change is None
    text = texts.render_outcome(finished, 10)
    assert "Турнир" not in text
    assert "(+1 в рейтинг)\n\n🏆" in text


def test_draw_does_not_touch_tournament(repo):
    service = DuelService(repo, logging.getLogger("tournament"), top_limit=10)
    t = repo.start_tournament(CHAT_ID, "Осень")
    session = _surrender_outcome().session

    finished = service.finish(DuelOutcome(OutcomeKind.DRAW, session))

    assert finished.rating_change is None
    assert repo.get_tournament_standings(t.tournament_id) == []


def test_tournament_texts_escape_html():
    standings_text = texts.render_tournament_table("<b>x</b>", [])
    change = texts.render_rating_change(
        RatingChange("A & B", 1516, 1484, 16), Player(1, "<i>w</i>", 1), Player(2, "l", 0)
    )

    assert "&lt;b&gt;x&lt;/b&gt;" in standings_text
    assert "A &amp; B" in change
    assert "&lt;i&gt;w&lt;/i&gt;" in change


def test_empty_tournament_table_says_no_duels():
    assert texts.render_tournament_table("Осень", []) == "🏟 <b>ТУРНИР «Осень»</b>\n\nВ турнире не было ни одного боя."


# --- commands ---


@pytest.fixture
def env(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "cmd.db"))
    repo.init()
    logger = logging.getLogger("tournament")
    config = Config(bot_token=TOKEN, admin_id=OWNER_ID, default_hp=2)
    engine = DuelEngine(logger=logger, default_hp=config.default_hp)

    async def get_chat_member(chat_id, user_id):
        user = User(id=user_id, is_bot=False, first_name="U")
        return (
            ChatMemberAdministrator.model_construct(user=user) if user_id == ALICE.id else ChatMemberMember(user=user)
        )

    bot = AsyncMock(spec=Bot)
    bot.id = 999
    bot.get_chat_member.side_effect = get_chat_member
    dp = build_dispatcher(config, repo, engine, logger)

    yield dp, bot, repo, engine
    cancel_timers(engine)
    router._parent_router = None


def _end_cb(admin: User, action: ConfirmAction, tournament_id: int = 1) -> str:
    return TournamentEndCb(action=action, admin_id=admin.id, tournament_id=tournament_id).pack()


async def test_non_admin_cannot_start_or_end(env):
    dp, bot, repo, _ = env
    repo.start_tournament(OTHER_CHAT, "Чужой")

    await dp.feed_update(bot, make_msg_update(1, BOB, "/tstart Осень"))
    await dp.feed_update(bot, make_msg_update(2, BOB, "/tend"))

    assert [m.text for m in sent_messages(bot)] == [texts.TOURNAMENT_DENIED] * 2
    assert repo.get_active_tournament(CHAT_ID) is None


async def test_admin_starts_tournament_once(env):
    dp, bot, repo, _ = env

    await dp.feed_update(bot, make_msg_update(1, ALICE, "/tstart   Осенний   кубок "))
    await dp.feed_update(bot, make_msg_update(2, ALICE, "/tstart Зима"))

    assert repo.get_active_tournament(CHAT_ID).title == "Осенний кубок"
    assert [m.text for m in sent_messages(bot)] == [
        texts.render_tournament_started("Осенний кубок", "@alice"),
        texts.render_tournament_already_open("Осенний кубок"),
    ]


async def test_bot_owner_starts_tournament_without_group_admin_rights(env):
    dp, bot, repo, _ = env
    owner = User(id=OWNER_ID, is_bot=False, first_name="Owner")

    await dp.feed_update(bot, make_msg_update(1, owner, "/tstart Осень"))

    assert repo.get_active_tournament(CHAT_ID).title == "Осень"


async def test_tournament_title_has_default_and_is_capped(env):
    dp, bot, repo, _ = env

    await dp.feed_update(bot, make_msg_update(1, ALICE, "/tstart"))
    assert repo.get_active_tournament(CHAT_ID).title.startswith("Турнир ")

    repo.end_tournament(CHAT_ID)
    await dp.feed_update(bot, make_msg_update(2, ALICE, "/tstart " + "Я" * 100))
    assert repo.get_active_tournament(CHAT_ID).title == "Я" * texts.TOURNAMENT_TITLE_MAX


async def test_duel_during_tournament_counts_in_both_tops(env):
    dp, bot, repo, engine = env
    t = repo.start_tournament(CHAT_ID, "Осень")
    make_invite(engine, msg_id=710)  # Alice challenges Bob

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, BOB, ACCEPT, msg_id=710))
        await dp.feed_update(bot, make_callback_update(2, BOB, SURRENDER, msg_id=710))
        await dp.feed_update(bot, make_callback_update(3, BOB, SURRENDER, msg_id=710))

    assert "Турнир «Осень»" in mock_edit.call_args.kwargs["text"]
    assert [(s.nickname, s.wins) for s in repo.get_top(CHAT_ID)] == [("@alice", 1), ("@bob", 0)]
    assert [(s.nickname, s.rating) for s in repo.get_tournament_standings(t.tournament_id)] == [
        ("@alice", 1516),
        ("@bob", 1484),
    ]


async def test_ttop_shows_table_or_no_tournament(env):
    dp, bot, repo, _ = env

    await dp.feed_update(bot, make_msg_update(1, TROLL, "/ttop"))
    t = repo.start_tournament(CHAT_ID, "Осень")
    repo.record_tournament_duel(CHAT_ID, _alice(), _bob())
    await dp.feed_update(bot, make_msg_update(2, TROLL, "/ttop"))

    standings = repo.get_tournament_standings(t.tournament_id)
    assert [m.text for m in sent_messages(bot)] == [
        texts.NO_TOURNAMENT,
        texts.render_tournament_table("Осень", standings),
    ]


async def test_admin_ends_tournament_after_confirm(env):
    dp, bot, repo, _ = env
    t = repo.start_tournament(CHAT_ID, "Осень")
    repo.record_tournament_duel(CHAT_ID, _alice(), _bob())

    await dp.feed_update(bot, make_msg_update(1, ALICE, "/tend"))
    (card,) = sent_messages(bot)
    assert card.text == texts.render_tournament_end_confirm("Осень")
    assert repo.get_active_tournament(CHAT_ID) is not None  # nothing is closed before the confirmation

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(
            bot, make_callback_update(2, BOB, _end_cb(ALICE, ConfirmAction.CONFIRM, t.tournament_id), msg_id=CARD_ID)
        )
        assert repo.get_active_tournament(CHAT_ID) is not None  # someone else's button
        await dp.feed_update(
            bot, make_callback_update(3, ALICE, _end_cb(ALICE, ConfirmAction.CONFIRM, t.tournament_id), msg_id=CARD_ID)
        )

    assert repo.get_active_tournament(CHAT_ID) is None
    final = mock_edit.call_args.kwargs["text"]
    assert final == texts.render_tournament_table("Осень", repo.get_tournament_standings(t.tournament_id), final=True)
    assert "👑 Победитель турнира: <b>@alice</b>!" in final
    assert (callback_answers(bot)[0].text, callback_answers(bot)[0].show_alert) == (texts.TOP_RESET_NOT_YOURS, True)


async def test_stale_end_card_does_not_close_next_tournament(env):
    dp, bot, repo, _ = env
    repo.start_tournament(CHAT_ID, "Осень")
    await dp.feed_update(bot, make_msg_update(1, ALICE, "/tend"))
    (card,) = sent_messages(bot)
    confirm = card.reply_markup.inline_keyboard[0][0].callback_data
    repo.end_tournament(CHAT_ID)  # «Осень» closed by another admin's card meanwhile
    repo.start_tournament(CHAT_ID, "Зима")

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(2, ALICE, confirm, msg_id=CARD_ID))

    assert repo.get_active_tournament(CHAT_ID).title == "Зима"
    assert mock_edit.call_args.kwargs["text"] == texts.TOURNAMENT_ALREADY_CLOSED


async def test_end_cancel_keeps_tournament(env):
    dp, bot, repo, _ = env
    repo.start_tournament(CHAT_ID, "Осень")

    with patch.object(Message, "edit_text", new=AsyncMock()) as mock_edit:
        await dp.feed_update(bot, make_callback_update(1, ALICE, _end_cb(ALICE, ConfirmAction.CANCEL), msg_id=CARD_ID))

    assert repo.get_active_tournament(CHAT_ID) is not None
    assert mock_edit.call_args.kwargs["text"] == texts.TOURNAMENT_END_CANCELLED


async def test_end_without_tournament(env):
    dp, bot, _, _ = env

    await dp.feed_update(bot, make_msg_update(1, ALICE, "/tend"))

    assert [m.text for m in sent_messages(bot)] == [texts.NO_TOURNAMENT]
