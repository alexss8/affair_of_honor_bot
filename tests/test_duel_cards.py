"""Карточка боя — «последнее состояние побеждает»: правки, пришедшие во время идущей правки, сливаются в одну свежую."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from helpers import FakeClock, max_in_window

from bot.domain.engine import DuelEngine
from bot.services.duel_service import DuelService
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.duel_cards import announce_outcome, card_message, refresh_card
from bot.telegram.rate_limiter import CHAT_LIMIT, CHAT_PERIOD_SEC, GROUP_LIMIT, GROUP_PERIOD_SEC, RateLimiter
from bot.telegram.ui import texts

CHAT = -100123

logger = logging.getLogger("test_duel_cards")


@pytest.fixture
def engine():
    return DuelEngine(logger=logger, default_hp=5, invite_ttl_sec=300)


@pytest.fixture
def session(engine):
    return engine.start_duel(CHAT, 100, 10, "Alice", 20, "Bob")


@pytest.fixture
def service(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "duels.db"))
    repo.init()
    yield DuelService(repo=repo, logger=logger, top_limit=10)
    repo.close()


class SlowCard:
    """Карточка, у которой первая правка «висит в сети», пока тест не отпустит её."""

    def __init__(self, bot=None) -> None:
        self.release = asyncio.Event()
        self.sent: list[str] = []
        self.message = MagicMock()
        self.message.chat.id = CHAT
        self.message.bot = bot if bot is not None else MagicMock()
        self.message.edit_text = AsyncMock(side_effect=self._edit_text)
        self.message.edit_reply_markup = AsyncMock(side_effect=self._edit_markup)
        self.first_error: Exception | None = None

    async def _edit_text(self, text, **kwargs):
        if not self.sent and not self.release.is_set():
            self.sent.append(text)
            await self.release.wait()
            if self.first_error is not None:
                raise self.first_error
            return True
        self.sent.append(text)
        return True

    async def _edit_markup(self, **kwargs):
        self.sent.append("<markup>")
        return True


def quick(coro):
    """Слитая правка должна вернуться сразу; на старом коде она ждала бы идущую правку вечно."""
    return asyncio.wait_for(coro, timeout=1)


async def started(coro) -> asyncio.Task:
    """Запускает правку и даёт ей дойти до отправки."""
    task = asyncio.create_task(coro)
    for _ in range(5):
        await asyncio.sleep(0)
    return task


async def test_refreshes_during_inflight_edit_merge_into_one(session):
    card = SlowCard()
    first = await started(refresh_card(card.message, session))

    merged = [await quick(refresh_card(card.message, session)) for _ in range(5)]
    card.release.set()

    assert await first is True
    assert merged == [True] * 5
    assert len(card.sent) == 2


async def test_merged_edit_shows_current_state(engine, session):
    card = SlowCard()
    first = await started(refresh_card(card.message, session))

    session.p2.hp = 2
    session.attacker_id = session.p2.id
    session.last_log = "Боб получил по шлему"
    await quick(refresh_card(card.message, session))
    card.release.set()
    await first

    assert card.sent[-1] == texts.render_battle(session)
    assert card.sent[0] != card.sent[-1]


async def test_outcome_is_sent_last_and_never_merged(engine, session, service):
    card = SlowCard()
    first = await started(refresh_card(card.message, session))

    await quick(refresh_card(card.message, session))
    outcome = engine.surrender(session, session.p1)
    finish = asyncio.create_task(announce_outcome(card.message, outcome, service))
    await asyncio.sleep(0)
    card.release.set()
    await asyncio.gather(first, finish)

    assert "ДУЭЛЬ #1 ОКОНЧЕНА" in card.sent[-1]
    assert len(card.sent) == 2  # первая правка и итог; слитая правка после итога не отправлена


async def test_outcome_waits_for_inflight_edit(engine, session, service):
    card = SlowCard()
    first = await started(refresh_card(card.message, session))

    finish = await started(announce_outcome(card.message, engine.surrender(session, session.p1), service))
    assert len(card.sent) == 1  # правка боя ещё в сети — итог не отправлен, иначе она его перезапишет
    card.release.set()
    await asyncio.gather(first, finish)

    assert len(card.sent) == 2
    assert "ДУЭЛЬ #1 ОКОНЧЕНА" in card.sent[-1]


async def test_deleted_card_reported_by_editor_only(session):
    card = SlowCard()
    card.first_error = TelegramBadRequest(
        method=EditMessageText(chat_id=CHAT, text="x"), message="Bad Request: message to edit not found"
    )
    first = await started(refresh_card(card.message, session))

    merged = await quick(refresh_card(card.message, session))
    card.release.set()

    assert await first is False
    assert merged is True
    assert len(card.sent) == 1


async def test_sequential_refreshes_edit_every_time(session):
    card = SlowCard()
    card.release.set()

    expected = []
    for hp in (4, 3, 2):
        session.p1.hp = hp
        expected.append(texts.render_battle(session))
        assert await refresh_card(card.message, session) is True

    assert card.sent == expected


async def test_card_is_rendered_after_waiting_for_chat_slot(session):
    now = [0.0]

    async def sleep(delay):
        session.p2.hp = 1  # бой идёт, пока правка ждёт слота
        now[0] += delay
        await asyncio.sleep(0)

    limiter = RateLimiter(clock=lambda: now[0], sleep=sleep)
    request = AsyncMock(return_value=True)
    for i in range(CHAT_LIMIT):  # слоты чата исчерпаны
        await limiter(request, None, EditMessageText(chat_id=CHAT, message_id=1, text=str(i)))
    bot = Bot("123456:TEST")
    bot.session.middleware(limiter)
    card = SlowCard(bot=bot)
    card.release.set()

    assert await refresh_card(card.message, session) is True

    assert now[0] > 0
    assert card.sent == [texts.render_battle(session)]
    assert "❤️🖤🖤🖤🖤 Bob" in card.sent[0].splitlines()
    await bot.session.close()


async def test_outcomes_survive_skipped_edits_under_limits(engine, service):
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    bot = Bot("123456:TEST")
    bot.session.middleware(limiter)
    log: list[tuple[float, EditMessageText]] = []

    async def network(bot, method, timeout=None):
        log.append((clock.now, method))
        await asyncio.sleep(0)
        return True

    bot.session.make_request = network
    duels = 10
    rounds = 4
    round_edits_sent: list[int] = []
    late_was_waiting: list[bool] = []

    async def play(i: int) -> None:
        message_id = i + 1
        session = engine.start_duel(CHAT, message_id, 1000 + 2 * i, f"A{i}", 1001 + 2 * i, f"B{i}")
        message = card_message(bot, CHAT, message_id)
        edits = []
        for hp in range(rounds, 0, -1):  # ходы быстрее, чем карточка успевает обновиться
            session.p2.hp = hp
            edits.append(asyncio.create_task(refresh_card(message, session)))
            await asyncio.sleep(0)
        assert all(await asyncio.gather(*edits))
        round_edits_sent.append(sum(m.message_id == message_id for _, m in log))

        late = asyncio.create_task(refresh_card(message, session))  # последний ход: правка ждёт слота, бой кончается
        await asyncio.sleep(0)
        late_was_waiting.append(not late.done())
        await announce_outcome(message, engine.surrender(session, session.p2), service)
        assert await late

    await asyncio.gather(*(play(i) for i in range(duels)))
    await bot.session.close()

    assert sum(round_edits_sent) < duels * rounds  # часть промежуточных правок слита
    assert any(late_was_waiting)
    for message_id in range(1, duels + 1):
        texts_sent = [m.text for _, m in log if m.message_id == message_id]
        assert "ОКОНЧЕНА" in texts_sent[-1]
        assert sum("ОКОНЧЕНА" in t for t in texts_sent) == 1
    times = [t for t, _ in log]
    assert max_in_window(times, CHAT_PERIOD_SEC) <= CHAT_LIMIT
    assert max_in_window(times, GROUP_PERIOD_SEC) <= GROUP_LIMIT
