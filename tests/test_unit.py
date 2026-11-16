import logging
import pytest
from engine import DuelEngine
from repository import SQLiteRepository
import ui

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

    assert engine.cancel_user_duel(1, 999) is None
    assert engine.is_user_busy(1, 10) is True

    canceled = engine.cancel_user_duel(1, 20)
    assert canceled is not None
    assert canceled.message_id == 100
    assert engine.is_user_busy(1, 10) is False
    assert engine.is_user_busy(1, 20) is False
    assert repo.get_top(1) == []

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

def test_render_battle_status_icons_and_persistent_log(engine):
    session = engine.start_duel(chat_id=1, msg_id=100, p1_id=10, p1_name="Alice", p2_id=20, p2_name="Bob")

    # 1. Начало боя: красный кружок у атакующего, зеленый у защищающегося, стартовое сообщение
    initial_text = ui.render_battle(session)
    assert "🔴 Атакует: <b>Alice</b>" in initial_text
    assert "🟢 Защищается: <b>Bob</b>" in initial_text
    assert "Бой начался! Выбирайте действия." in initial_text

    # 2. Атакующий выбрал удар -> красный кружок сменился на галочку, фраза осталась
    session.p1.choice = "top"
    after_att_choice = ui.render_battle(session)
    assert "✅ Атакует: <b>Alice</b>" in after_att_choice
    assert "🟢 Защищается: <b>Bob</b>" in after_att_choice
    assert "Бой начался! Выбирайте действия." in after_att_choice

    # 3. Защищающийся выбрал блок -> оба с галочками, фраза все еще не пропала
    session.p2.choice = "bot"
    both_chosen_text = ui.render_battle(session, is_rolling=True)
    assert "✅ Атакует: <b>Alice</b>" in both_chosen_text
    assert "✅ Защищается: <b>Bob</b>" in both_chosen_text
    assert "Бой начался! Выбирайте действия." in both_chosen_text

    # 4. Раунд завершился -> роли поменялись, кружки сбросились, фраза обновилась на новую
    round_log, is_over = engine.resolve_round(session)
    assert is_over is False
    next_round_text = ui.render_battle(session)
    assert "🔴 Атакует: <b>Bob</b>" in next_round_text
    assert "🟢 Защищается: <b>Alice</b>" in next_round_text
    assert round_log in next_round_text
    assert "Бой начался! Выбирайте действия." not in next_round_text

    # 5. В новом раунде защитник выбрал блок -> зеленый кружок сменился на галочку, фраза прошлого раунда сохранилась
    session.p1.choice = "top"
    after_def_choice = ui.render_battle(session)
    assert "🔴 Атакует: <b>Bob</b>" in after_def_choice
    assert "✅ Защищается: <b>Alice</b>" in after_def_choice
    assert round_log in after_def_choice
