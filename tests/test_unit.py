import asyncio
import html
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from helpers import TOKEN

import bot.__main__ as bot_main
from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import ChoiceResult, DuelInvite, DuelPhase, MoveKind, OutcomeKind, Player, PlayerStats, Zone
from bot.services.duel_service import DuelService
from bot.storage.queued_repo import QueuedRepository
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.callbacks import InviteAction, InviteCb, MoveCb
from bot.telegram.handlers import router
from bot.telegram.ui import keyboards, texts

LOGGER = logging.getLogger("test_logger")


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteRepository(str(tmp_path / "test_duels.db"))
    repository.init()
    return repository


@pytest.fixture
def engine():
    return DuelEngine(logger=LOGGER, default_hp=5, invite_ttl_sec=60)


@pytest.fixture
def service(repo):
    return DuelService(repo=repo, logger=LOGGER, top_limit=10)


@pytest.fixture
def session(engine):
    return engine.start_duel(chat_id=1, msg_id=100, p1_id=10, p1_name="Alice", p2_id=20, p2_name="Bob")


def play_round(engine, session, attack: Zone, defend: Zone):
    assert engine.choose(session, session.attacker, MoveKind.ATTACK, attack) == (ChoiceResult.ACCEPTED, None)
    result, outcome = engine.choose(session, session.defender, MoveKind.DEFEND, defend)
    assert result is ChoiceResult.ROUND_RESOLVED
    return outcome


# ---------- Репозиторий ----------


def test_user_cache_and_lookup(repo):
    repo.save_user(chat_id=-1001, user_id=42, username="Legolas_Greenleaf", first_name="Леголас")

    assert repo.get_user_by_username(-1001, "@legolas_greenleaf") == (42, "@legolas_greenleaf")
    assert repo.get_user_by_username(-1001, "LEGOLAS_GREENLEAF") == (42, "@legolas_greenleaf")
    assert repo.get_user_by_username(-1001, "@unknown") is None

    repo.save_user(chat_id=-1001, user_id=42, username="NewNick", first_name="Леголас")
    assert repo.get_user_by_username(-1001, "@newnick") == (42, "@newnick")
    assert repo.get_user_by_username(-1001, "@legolas_greenleaf") is None


def test_close_checkpoints_wal_into_main_file(tmp_path):
    db_path = tmp_path / "close.db"
    repository = SQLiteRepository(str(db_path))
    repository.init()
    repository.record_duel(1, Player(10, "Alice", 5), Player(20, "Bob", 0))
    assert Path(f"{db_path}-wal").exists()

    repository.close()
    repository.close()  # повторный вызов безопасен

    assert not Path(f"{db_path}-wal").exists()
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT nickname, wins FROM stats WHERE user_id = 10").fetchone() == ("Alice", 1)


async def test_main_closes_database_after_flushing_queue(tmp_path):
    """Штатная остановка: сначала дописывается фоновая очередь, затем закрывается соединение с базой."""
    config = Config(bot_token=TOKEN, db_path=str(tmp_path / "main.db"), log_file=str(tmp_path / "main.log"))
    calls = []
    real_stop, real_close = QueuedRepository.stop_worker, SQLiteRepository.close

    async def spy_stop(self):
        await real_stop(self)
        calls.append("stop_worker")

    def spy_close(self):
        calls.append("close")
        real_close(self)

    with (
        patch.object(bot_main, "setup_logger", return_value=LOGGER),
        patch.object(Bot, "set_my_commands", AsyncMock()),
        patch.object(Bot, "delete_webhook", AsyncMock()),
        patch.object(Dispatcher, "start_polling", AsyncMock()),
        patch.object(QueuedRepository, "stop_worker", spy_stop),
        patch.object(SQLiteRepository, "close", spy_close),
    ):
        try:
            await bot_main.main(config)
        finally:
            router._parent_router = None

    assert calls == ["stop_worker", "close"]
    assert not Path(f"{config.db_path}-wal").exists()


def test_suspended_state_roundtrip_freezes_time(repo, engine):
    """Снимок боёв и вызовов переживает рестарт; оставшееся время отсчитывается от момента загрузки."""
    session = engine.start_duel(1, 100, 10, "Alice", 20, "Bob")
    session.p1.hp, session.p2.choice = 3, Zone.BOT
    session.attacker_id, session.last_log = 20, "Удар!"
    session.deadline = 1000.0 + 120
    no_timer = engine.start_duel(1, 101, 30, "Carol", 40, "Dave")
    invite = engine.create_invite(2, 200, 50, "Eve", 60, "Frank", now=1000.0)  # ttl 60

    repo.save_suspended([session, no_timer], [invite], now=1000.0)
    sessions, invites = repo.pop_suspended(now=5000.0)

    restored = {s.message_id: s for s in sessions}
    s = restored[100]
    assert (s.chat_id, s.p1.id, s.p1.name, s.p1.hp, s.p1.choice) == (1, 10, "Alice", 3, None)
    assert (s.p2.id, s.p2.name, s.p2.hp, s.p2.choice) == (20, "Bob", 5, Zone.BOT)
    assert (s.attacker_id, s.max_hp, s.last_log, s.deadline) == (20, 5, "Удар!", 5120.0)
    assert restored[101].deadline is None
    assert len(invites) == 1
    inv = invites[0]
    assert (inv.chat_id, inv.message_id, inv.challenger_id, inv.challenger_name) == (2, 200, 50, "Eve")
    assert (inv.target_id, inv.target_name, inv.expires_at) == (60, "Frank", 5060.0)
    assert repo.pop_suspended(now=5000.0) == ([], [])


def test_suspended_snapshot_survives_new_column_from_future_migration(repo, engine):
    """Будущая миграция может добавить колонку в таблицы снимка: запись и чтение идут по именам колонок."""
    for table in ("suspended_duels", "suspended_invites"):
        repo._conn.execute(f"ALTER TABLE {table} ADD COLUMN extra TEXT")
    session = engine.start_duel(1, 100, 10, "Alice", 20, "Bob")
    session.p2.choice, session.last_log, session.deadline = Zone.TOP, "Удар!", 130.0
    invite = engine.create_invite(2, 200, 50, "Eve", 60, "Frank", now=100.0)

    repo.save_suspended([session], [invite], now=100.0)
    sessions, invites = repo.pop_suspended(now=1000.0)

    s = sessions[0]
    assert (s.chat_id, s.message_id, s.p1.name, s.p2.choice, s.last_log, s.deadline) == (
        1,
        100,
        "Alice",
        Zone.TOP,
        "Удар!",
        1030.0,
    )
    inv = invites[0]
    assert (inv.message_id, inv.challenger_name, inv.target_name, inv.expires_at) == (200, "Eve", "Frank", 1060.0)


def test_restore_registers_sessions_and_invites(engine, repo):
    both_chose = engine.start_duel(1, 100, 10, "Alice", 20, "Bob")  # снимок старой версии, остановленной на броске
    both_chose.p1.choice = both_chose.p2.choice = Zone.TOP
    choosing = engine.start_duel(1, 101, 30, "Carol", 40, "Dave")
    invite = engine.create_invite(1, 200, 50, "Eve", 60, "Frank")
    repo.save_suspended(*engine.interrupt_all(), now=0.0)
    sessions, invites = repo.pop_suspended(now=0.0)

    engine.restore(sessions, invites)

    replayed = engine.get_session(1, 100)
    assert replayed.phase is DuelPhase.CHOOSING
    assert replayed.p1.choice is None and replayed.p2.choice is None  # раунд переигрывается
    assert (replayed.p1.hp, replayed.p2.hp) == (5, 5)
    assert engine.get_session(1, 101).phase is DuelPhase.CHOOSING
    assert engine.is_user_busy(1, 40) is True
    assert engine.get_invite(1, 200).challenger_id == invite.challenger_id
    assert choosing.phase is DuelPhase.FINISHED  # старые объекты не воскресают


# ---------- Приглашения ----------


def test_accept_invite_starts_duel_once(engine):
    invite = engine.create_invite(1, 100, 10, "Alice", 20, "Bob", now=0)

    session = engine.accept_invite(invite)
    assert session is not None
    assert (session.p1.name, session.p2.name) == ("Alice", "Bob")
    assert engine.get_invite(1, 100) is None
    assert engine.accept_invite(invite) is None  # повторное принятие


def test_accept_invite_refused_when_player_busy(engine):
    engine.start_duel(1, 900, 10, "Alice", 30, "Troll")
    invite = engine.create_invite(1, 100, 10, "Alice", 20, "Bob")

    assert engine.accept_invite(invite) is None
    assert engine.get_invite(1, 100) is invite  # решение о закрытии остаётся за обработчиком


def test_invite_expiry(engine):
    old = engine.create_invite(1, 100, 10, "Alice", 20, "Bob", now=0)
    assert not old.is_expired(59)
    assert old.is_expired(60)


def test_second_open_invite_from_same_challenger_is_refused(engine):
    assert engine.reserve_invite(1, 10, now=0) is True
    engine.create_invite(1, 100, 10, "Alice", 20, "Bob", now=0)

    assert engine.reserve_invite(1, 10, now=1) is False


def test_reserved_invite_counts_as_open_until_card_is_sent(engine):
    assert engine.reserve_invite(1, 10, now=0) is True
    assert engine.reserve_invite(1, 10, now=0) is False  # второй /duel, пока карточка первого отправляется


def test_new_invite_allowed_elsewhere_and_after_first_is_closed(engine):
    invite = engine.create_invite(1, 100, 10, "Alice", 20, "Bob", now=0)

    assert engine.reserve_invite(2, 10, now=1) is True  # другой чат
    assert engine.reserve_invite(1, 30, now=1) is True  # другой инициатор тому же сопернику
    assert engine.reserve_invite(1, 10, now=60) is True  # первый вызов истёк
    engine.release_invite_reservation(1, 10)
    assert engine.close_invite(invite) is True
    assert engine.reserve_invite(1, 10, now=1) is True  # первый вызов закрыт


def test_released_reservation_allows_next_invite(engine):
    assert engine.reserve_invite(1, 10, now=0) is True
    engine.release_invite_reservation(1, 10)  # карточку отправить не удалось

    assert engine.reserve_invite(1, 10, now=0) is True


def test_close_invite_reports_whether_it_was_open(engine):
    invite = engine.create_invite(1, 100, 10, "Alice", 20, "Bob")
    assert engine.close_invite(invite) is True
    assert engine.close_invite(invite) is False


# ---------- Ходы и фазы ----------


def test_choice_rules(engine, session):
    alice, bob = session.p1, session.p2  # Alice атакует первой
    assert engine.choose(session, alice, MoveKind.DEFEND, Zone.TOP) == (ChoiceResult.MUST_ATTACK, None)
    assert engine.choose(session, bob, MoveKind.ATTACK, Zone.TOP) == (ChoiceResult.MUST_DEFEND, None)
    assert engine.choose(session, alice, MoveKind.ATTACK, Zone.TOP) == (ChoiceResult.ACCEPTED, None)
    assert engine.choose(session, alice, MoveKind.ATTACK, Zone.TOP) == (ChoiceResult.ALREADY_CHOSEN, None)
    assert engine.choose(session, alice, MoveKind.ATTACK, Zone.BOT) == (ChoiceResult.ACCEPTED, None)  # передумала
    assert engine.choose(session, bob, MoveKind.DEFEND, Zone.TOP) == (ChoiceResult.ROUND_RESOLVED, None)


def test_second_choice_resolves_round_at_once(engine, session):
    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)

    assert engine.choose(session, session.p2, MoveKind.DEFEND, Zone.BOT) == (ChoiceResult.ROUND_RESOLVED, None)

    assert session.p2.hp == 4
    assert session.attacker_id == 20
    assert session.p1.choice is None and session.p2.choice is None
    assert session.phase is DuelPhase.CHOOSING
    assert "Alice" in session.last_log


def test_second_choice_returns_knockout(engine, session):
    session.p2.hp = 1
    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)

    result, outcome = engine.choose(session, session.p2, MoveKind.DEFEND, Zone.BOT)

    assert result is ChoiceResult.ROUND_RESOLVED
    assert outcome.kind is OutcomeKind.KNOCKOUT
    assert outcome.winner is session.p1 and outcome.loser is session.p2
    assert session.phase is DuelPhase.FINISHED
    assert engine.get_session(1, 100) is None


def test_choose_in_finished_duel_raises(engine, session):
    engine.surrender(session, session.p2)

    with pytest.raises(ValueError):
        engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)


def test_block_prevents_damage(engine, session):
    assert play_round(engine, session, Zone.TOP, Zone.TOP) is None

    assert session.p2.hp == 5
    assert session.attacker_id == 20
    assert session.p1.choice is None and session.p2.choice is None
    assert session.phase is DuelPhase.CHOOSING


def test_knockout_and_service_records_result(engine, service, repo, session):
    outcome = None
    while outcome is None:
        outcome = play_round(engine, session, Zone.TOP, Zone.BOT)  # каждый удар проходит

    assert outcome.kind is OutcomeKind.KNOCKOUT
    assert outcome.winner is session.p1 and outcome.loser is session.p2
    assert session.p2.hp == 0 and session.p1.hp == 1
    assert session.phase is DuelPhase.FINISHED
    assert engine.is_user_busy(1, 10) is False

    finished = service.finish(outcome)
    assert finished.duel_id == 1
    assert [(s.nickname, s.wins, s.losses) for s in finished.top] == [("Alice", 1, 0), ("Bob", 0, 1)]


def test_surrender_needs_confirmation(engine, session):
    bob = session.p2
    assert engine.request_surrender(session, bob) is False
    assert session.surrender_confirm_id == 20
    assert engine.request_surrender(session, bob) is True

    outcome = engine.surrender(session, bob)
    assert outcome.kind is OutcomeKind.SURRENDER
    assert outcome.winner is session.p1 and outcome.loser is bob
    assert engine.get_session(1, 100) is None


def test_choice_resets_surrender_confirmation(engine, session):
    engine.request_surrender(session, session.p1)
    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)
    assert session.surrender_confirm_id is None


def test_timeout_outcomes(engine):
    draw_session = engine.start_duel(1, 100, 10, "Alice", 20, "Bob")
    draw = engine.resolve_timeout(draw_session)
    assert draw.kind is OutcomeKind.DRAW
    assert draw.winner is None and not draw.is_rated
    assert engine.resolve_timeout(draw_session) is None  # уже завершена

    win_session = engine.start_duel(1, 101, 10, "Alice", 20, "Bob")
    win_session.p1.hp = 2
    win = engine.resolve_timeout(win_session)
    assert win.kind is OutcomeKind.TIMEOUT
    assert win.winner is win_session.p2 and win.loser is win_session.p1


def test_cancel_duel_frees_players_without_rating(engine, repo, session):
    assert engine.is_user_busy(1, 10) is True
    assert engine.cancel_user_duel(1, 999) is None

    canceled = engine.cancel_user_duel(1, 20)
    assert canceled is session
    assert session.phase is DuelPhase.FINISHED
    assert engine.is_user_busy(1, 10) is False
    assert engine.is_user_busy(1, 20) is False
    assert repo.get_top(1) == []


async def test_interrupt_all_clears_sessions_and_invites(engine):
    session = engine.start_duel(1, 100, 10, "Alice", 20, "Bob")
    timer = asyncio.create_task(asyncio.sleep(60))
    session.timeout_task = timer
    invite = engine.create_invite(1, 200, 30, "Carol", 40, "Dave")

    sessions, invites = engine.interrupt_all()
    await asyncio.sleep(0)

    assert sessions == [session] and invites == [invite]
    assert session.phase is DuelPhase.FINISHED
    assert timer.cancelled()
    assert engine.sessions == {} and engine.invites == {}
    assert engine.interrupt_all() == ([], [])


def test_service_does_not_record_draw(engine, service, repo):
    draw = engine.resolve_timeout(engine.start_duel(1, 100, 10, "Alice", 20, "Bob"))
    finished = service.finish(draw)
    assert finished.duel_id is None
    assert repo.get_top(1) == []


# ---------- Callback data ----------


def test_callback_data_roundtrip():
    packed = MoveCb(kind=MoveKind.DEFEND, zone=Zone.BOT).pack()
    assert len(packed.encode()) <= 64  # лимит Telegram
    assert MoveCb.unpack(packed) == MoveCb(kind=MoveKind.DEFEND, zone=Zone.BOT)
    assert InviteCb.unpack(InviteCb(action=InviteAction.ACCEPT).pack()).action is InviteAction.ACCEPT


# ---------- Тексты ----------


def test_render_battle_status_icons_and_persistent_log(engine, session):
    # 1. Начало боя: красный кружок у атакующего, зеленый у защищающегося, стартовое сообщение
    initial_text = texts.render_battle(session)
    assert "🔴 Атакует: <b>Alice</b>" in initial_text
    assert "🟢 Защищается: <b>Bob</b>" in initial_text
    assert "Бой начался! Выбирайте действия." in initial_text

    # 2. Атакующий выбрал удар -> красный кружок сменился на галочку, фраза осталась
    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)
    after_att_choice = texts.render_battle(session)
    assert "✅ Атакует: <b>Alice</b>" in after_att_choice
    assert "🟢 Защищается: <b>Bob</b>" in after_att_choice
    assert "Бой начался! Выбирайте действия." in after_att_choice

    # 3. Защищающийся выбрал блок -> раунд сразу рассчитан: роли поменялись, кружки сбросились, фраза новая
    engine.choose(session, session.p2, MoveKind.DEFEND, Zone.BOT)
    next_round_text = texts.render_battle(session)
    assert "🔴 Атакует: <b>Bob</b>" in next_round_text
    assert "🟢 Защищается: <b>Alice</b>" in next_round_text
    assert session.last_log in next_round_text
    assert "Бой начался! Выбирайте действия." not in next_round_text

    # 4. В новом раунде защитник выбрал блок -> зеленый кружок сменился на галочку, фраза прошлого раунда сохранилась
    engine.choose(session, session.p1, MoveKind.DEFEND, Zone.TOP)
    after_def_choice = texts.render_battle(session)
    assert "🔴 Атакует: <b>Bob</b>" in after_def_choice
    assert "✅ Защищается: <b>Alice</b>" in after_def_choice
    assert session.last_log in after_def_choice


def test_render_outcome_for_every_kind(engine, service):
    s1 = engine.start_duel(1, 1, 10, "Alice", 20, "Bob")
    text = texts.render_outcome(service.finish(engine.surrender(s1, s1.p2)), 10)
    assert "🏳️ <b>ДУЭЛЬ #1 ОКОНЧЕНА!</b>" in text and "Bob выбросил белый флаг" in text

    s2 = engine.start_duel(1, 2, 10, "Alice", 20, "Bob")
    s2.p2.hp = 1
    text = texts.render_outcome(service.finish(engine.resolve_timeout(s2)), 10)
    assert "⏰ <b>ДУЭЛЬ #2 ОКОНЧЕНА!</b>" in text and "оказался сильнее Bob" in text

    s3 = engine.start_duel(1, 3, 10, "Alice", 20, "Bob")
    text = texts.render_outcome(service.finish(engine.resolve_timeout(s3)), 10)
    assert "ВНИЧЬЮ" in text and "по 5 ❤️" in text

    s4 = engine.start_duel(1, 4, 10, "Alice", 20, "Bob")
    outcome = None
    while outcome is None:
        outcome = play_round(engine, s4, Zone.TOP, Zone.BOT)
    text = texts.render_outcome(service.finish(outcome), 10)
    assert "🏆 <b>ДУЭЛЬ #3 ОКОНЧЕНА!</b>" in text and s4.last_log in text
    assert "Победитель: <b>Alice</b>" in text


EVIL = "<i>x</i> & y"
EVIL_ESCAPED = "&lt;i&gt;x&lt;/i&gt; &amp; y"


def _assert_shown_literally(text: str) -> None:
    assert EVIL_ESCAPED in text
    assert EVIL not in text


def test_cards_escape_raw_names(engine, service):
    invite = DuelInvite(1, 50, 10, EVIL, 20, "Bob", expires_at=100.0)
    for text in (
        texts.render_invite(EVIL, "Bob"),
        texts.render_invite_paused(invite),
        texts.render_invite_interrupted(invite),
    ):
        _assert_shown_literally(text)

    session = engine.start_duel(1, 1, 10, EVIL, 20, "Bob")
    assert session.p1.name == EVIL
    for text in (
        texts.render_battle(session),
        texts.render_paused(session),
        texts.render_cancelled(session, EVIL),
        texts.render_interrupted(session),
    ):
        _assert_shown_literally(text)

    surrender = engine.start_duel(1, 2, 10, "Alice", 20, EVIL)
    _assert_shown_literally(texts.render_outcome(service.finish(engine.surrender(surrender, surrender.p2)), 10))

    timeout = engine.start_duel(1, 3, 10, "Alice", 20, EVIL)
    timeout.p1.hp = 1
    _assert_shown_literally(texts.render_outcome(service.finish(engine.resolve_timeout(timeout)), 10))

    knockout = engine.start_duel(1, 4, 10, EVIL, 20, "Bob")
    outcome = None
    while outcome is None:
        outcome = play_round(engine, knockout, Zone.TOP, Zone.BOT)
    assert EVIL in knockout.last_log
    finished = service.finish(outcome)
    assert EVIL in [s.nickname for s in finished.top]
    _assert_shown_literally(texts.render_outcome(finished, 10))
    _assert_shown_literally(texts.render_top(finished.top))


def test_button_shows_raw_name(engine):
    session = engine.start_duel(1, 1, 10, "Alice", 20, EVIL)
    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)

    footer = keyboards.battle_keyboard(session).inline_keyboard[-1][0].text
    assert EVIL in footer


LONG_NAME = "<b>Tom & Jerry</b> " + "Я" * 40
LONG_SHORT = "<b>Tom & Jerry</b> ЯЯЯЯ…"  # 23 символа + «…»


@pytest.mark.parametrize(
    "wins, expected",
    [
        (1, "<b>1</b> победа"),
        (2, "<b>2</b> победы"),
        (5, "<b>5</b> побед"),
        (11, "<b>11</b> побед"),
        (21, "<b>21</b> победа"),
    ],
)
def test_top_declines_wins(wins, expected):
    text = texts.render_top([PlayerStats("Alice", wins, 1)])
    assert f"Alice — {expected} / 1 пор." in text


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Alice", "Alice"),
        ("Я" * 24, "Я" * 24),
        ("Я" * 25, "Я" * 23 + "…"),
        ("Я" * 22 + " ЯЯЯ", "Я" * 22 + "…"),  # пробел перед «…» не оставляем
        (LONG_NAME, LONG_SHORT),
    ],
)
def test_short_name(name, expected):
    assert texts.short_name(name) == expected
    assert len(texts.short_name(name)) <= texts.NAME_MAX


def test_battle_card_hearts_first_and_names_truncated(engine):
    session = engine.start_duel(1, 1, 10, "Alice", 20, LONG_NAME)
    session.p2.hp = 3
    lines = texts.render_battle(session).splitlines()

    assert "❤️❤️❤️❤️❤️ Alice" in lines
    assert "❤️❤️❤️🖤🖤 " + html.escape(LONG_SHORT) in lines
    assert "🟢 Защищается: <b>" + html.escape(LONG_SHORT) + "</b>" in lines
    assert "Я" * 24 not in texts.render_battle(session)

    engine.choose(session, session.p1, MoveKind.ATTACK, Zone.TOP)
    footer = keyboards.battle_keyboard(session).inline_keyboard[-1][0].text
    assert LONG_SHORT in footer and "Я" * 24 not in footer


def test_cards_and_top_truncate_long_names(engine, service):
    invite = DuelInvite(1, 50, 10, LONG_NAME, 20, "Bob", expires_at=100.0)
    session = engine.start_duel(1, 1, 10, "Alice", 20, LONG_NAME)
    surrender = engine.start_duel(1, 2, 10, "Alice", 20, LONG_NAME)
    timeout = engine.start_duel(1, 3, 10, LONG_NAME, 20, "Bob")
    timeout.p2.hp = 1
    texts_to_check = [
        texts.render_invite(LONG_NAME, "Bob"),
        texts.render_invite_interrupted(invite),
        texts.render_cancelled(session, LONG_NAME),
        texts.render_interrupted(session),
        texts.render_outcome(service.finish(engine.surrender(surrender, surrender.p2)), 10),
        texts.render_outcome(service.finish(engine.resolve_timeout(timeout)), 10),
    ]
    for text in texts_to_check:
        assert html.escape(LONG_SHORT) in text
        assert "Я" * 24 not in text


@pytest.mark.parametrize(
    "n, expected",
    [
        (1, "жизнь"),
        (2, "жизни"),
        (4, "жизни"),
        (5, "жизней"),
        (11, "жизней"),
        (12, "жизней"),
        (21, "жизнь"),
        (22, "жизни"),
        (111, "жизней"),
        (0, "жизней"),
    ],
)
def test_plural(n, expected):
    assert texts.plural(n, "жизнь", "жизни", "жизней") == expected


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (300, "5 минут"),
        (60, "1 минута"),
        (120, "2 минуты"),
        (90, "90 секунд"),
        (45, "45 секунд"),
        (1, "1 секунда"),
    ],
)
def test_format_duration(seconds, expected):
    assert texts.format_duration(seconds) == expected


def test_help_uses_configured_timeout_and_hp():
    text = texts.render_help(default_hp=3, top_limit=10, duel_timeout_sec=180)
    assert "по <b>3 жизни</b>" in text
    assert "отводится <b>3 минуты</b>" in text
    assert "5 минут" not in text

    text = texts.render_help(default_hp=5, top_limit=10, duel_timeout_sec=300)
    assert "по <b>5 жизней</b>" in text
    assert "отводится <b>5 минут</b>" in text
