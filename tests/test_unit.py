import logging
import pytest
from engine import DuelEngine
from repository import SQLiteRepository

@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_duels.db"
    repository = SQLiteRepository(str(db_file))
    repository.init()
    return repository

@pytest.fixture
def engine(repo):
    logger = logging.getLogger("test_logger")
    return DuelEngine(repo=repo, logger=logger, default_hp=5)

def test_user_cache_and_lookup(repo):
    repo.save_user(chat_id=-1001, user_id=42, username="Legolas_Greenleaf", first_name="Леголас")

    assert repo.get_user_by_username(-1001, "@legolas_greenleaf") == (42, "@legolas_greenleaf")
    assert repo.get_user_by_username(-1001, "LEGOLAS_GREENLEAF") == (42, "@legolas_greenleaf")
    assert repo.get_user_by_username(-1001, "@unknown") is None

    repo.save_user(chat_id=-1001, user_id=42, username="NewNick", first_name="Леголас")
    assert repo.get_user_by_username(-1001, "@newnick") == (42, "@newnick")
    assert repo.get_user_by_username(-1001, "@legolas_greenleaf") is None

def test_cancel_duel_frees_players_without_rating(engine, repo):
    engine.start_duel(chat_id=1, msg_id=100, p1_id=10, p1_name="Alice", p2_id=20, p2_name="Bob")
    assert engine.is_user_busy(1, 10) is True
    assert engine.is_user_busy(1, 20) is True

    # Чужак не может отменить бой
    assert engine.cancel_user_duel(1, 999) is None
    assert engine.is_user_busy(1, 10) is True

    # Второй игрок отменяет бой
    canceled = engine.cancel_user_duel(1, 20)
    assert canceled is not None
    assert canceled.message_id == 100
    assert engine.is_user_busy(1, 10) is False
    assert engine.is_user_busy(1, 20) is False
    assert repo.get_top(1) == []  # Рейтинг остался пустым

def test_block_prevents_damage(engine):
    session = engine.start_duel(chat_id=1, msg_id=100, p1_id=10, p1_name="Alice", p2_id=20, p2_name="Bob")
    session.p1.choice = "top"
    session.p2.choice = "top"

    log, is_over = engine.resolve_round(session)

    assert is_over is False
    assert session.p2.hp == 5
    assert session.attacker_id == 20
    assert session.p1.choice is None

def test_hit_reduces_hp_and_finishes_game(engine, repo):
    session = engine.start_duel(chat_id=1, msg_id=100, p1_id=10, p1_name="Alice", p2_id=20, p2_name="Bob")

    is_over = False
    for _ in range(9):
        if session.attacker_id == 10:
            session.p1.choice = "top"
            session.p2.choice = "bot"
        else:
            session.p2.choice = "bot"
            session.p1.choice = "top"
        log, is_over = engine.resolve_round(session)

    assert is_over is True
    assert session.p2.hp == 0
    assert session.p1.hp == 1

    winner = session.p1 if session.p1.hp > 0 else session.p2
    loser = session.get_opponent(winner.id)
    duel_id = engine.finish_duel(session, winner, loser)

    assert duel_id == 1
    assert engine.is_user_busy(1, 10) is False
    top = repo.get_top(1)
    assert len(top) == 2
    assert top[0].nickname == "Alice"
    assert top[0].wins == 1
