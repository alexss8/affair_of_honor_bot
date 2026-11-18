"""Лимитер запросов к Telegram на подменённых часах: время сдвигает фейковый sleep, реальных пауз нет."""

import asyncio

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetChat, GetUpdates, SendMessage, SetMyCommands

from bot.__main__ import create_bot
from bot.telegram.rate_limiter import (
    CHAT_LIMIT,
    CHAT_PERIOD_SEC,
    GLOBAL_LIMIT,
    GLOBAL_PERIOD_SEC,
    GROUP_LIMIT,
    GROUP_PERIOD_SEC,
    RateLimiter,
)

GROUP = -100123
OTHER_GROUP = -100456


class FakeClock:
    """Часы и sleep для лимитера: sleep мгновенно сдвигает время вперёд.

    Лимитер считает паузу от текущего времени перед каждым sleep, поэтому одновременные паузы не складываются.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps += 1
        self.now += delay
        await asyncio.sleep(0)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(clock):
    return RateLimiter(clock=clock, sleep=clock.sleep)


def make_sender(clock: FakeClock, log: list):
    """Заглушка сети: записывает время и сам запрос."""

    async def make_request(bot, method):
        log.append((clock.now, method))
        return True

    return make_request


async def send_all(limiter, clock, methods) -> list:
    log: list = []
    request = make_sender(clock, log)
    await asyncio.gather(*(limiter(request, None, m) for m in methods))
    return log


def max_in_window(times: list[float], period: float) -> int:
    """Наибольшее число отправок в любом полуоткрытом окне [t, t + period)."""
    return max(sum(1 for u in times if t <= u < t + period) for t in times)


def edit(chat_id: int, n: int = 0) -> EditMessageText:
    return EditMessageText(chat_id=chat_id, message_id=1, text=f"правка {n}")


async def test_global_limit_across_chats(limiter, clock):
    log = await send_all(limiter, clock, [SendMessage(chat_id=1000 + i, text="x") for i in range(100)])

    assert len(log) == 100
    assert max_in_window([t for t, _ in log], GLOBAL_PERIOD_SEC) == GLOBAL_LIMIT


async def test_chat_limit_allows_short_burst(limiter, clock):
    log = await send_all(limiter, clock, [SendMessage(chat_id=42, text=str(i)) for i in range(10)])
    times = [t for t, _ in log]

    assert times[:CHAT_LIMIT] == [0.0] * CHAT_LIMIT
    assert max_in_window(times, CHAT_PERIOD_SEC) == CHAT_LIMIT
    assert len(times) == 10


async def test_group_limit_per_minute(limiter, clock):
    log = await send_all(limiter, clock, [edit(GROUP, i) for i in range(60)])

    assert len(log) == 60
    assert max_in_window([t for t, _ in log], GROUP_PERIOD_SEC) == GROUP_LIMIT


async def test_lowered_group_limit_applies_immediately(limiter, clock):
    limiter.group_limit = 20
    log = await send_all(limiter, clock, [edit(GROUP, i) for i in range(30)])

    assert max_in_window([t for t, _ in log], GROUP_PERIOD_SEC) == 20


async def test_requests_in_one_chat_keep_order(limiter, clock):
    log = await send_all(limiter, clock, [edit(GROUP, i) for i in range(12)])

    assert [m.text for _, m in log] == [f"правка {i}" for i in range(12)]


async def test_busy_chat_does_not_delay_other_chat(limiter, clock):
    log: list = []
    request = make_sender(clock, log)
    busy = [asyncio.create_task(limiter(request, None, edit(GROUP, i))) for i in range(10)]
    await asyncio.sleep(0)
    assert not all(task.done() for task in busy)  # первая группа упёрлась в лимит и ждёт
    called_at, sleeps_before = clock.now, clock.sleeps

    await limiter(request, None, edit(OTHER_GROUP))

    assert (called_at, OTHER_GROUP) in [(t, m.chat_id) for t, m in log]
    assert clock.sleeps == sleeps_before  # запрос во вторую группу не ждал
    await asyncio.gather(*busy)


@pytest.mark.parametrize(
    "method",
    [
        AnswerCallbackQuery(callback_query_id="1", text="ok"),
        GetUpdates(),
        GetChat(chat_id=GROUP),
        SetMyCommands(commands=[]),
    ],
    ids=lambda m: m.__api_method__,
)
async def test_exempt_methods_pass_instantly_when_limits_are_full(limiter, clock, method):
    await send_all(limiter, clock, [edit(GROUP, i) for i in range(GROUP_LIMIT)])  # окна чата и группы заполнены
    start, sleeps_before = clock.now, clock.sleeps

    log = await send_all(limiter, clock, [method])

    assert log == [(start, method)]
    assert clock.sleeps == sleeps_before


async def test_idle_chat_state_is_dropped(limiter, clock):
    await send_all(limiter, clock, [edit(GROUP), SendMessage(chat_id=42, text="x")])
    clock.now += GROUP_PERIOD_SEC + 1

    await send_all(limiter, clock, [SendMessage(chat_id=7, text="x")])

    assert set(limiter._chats) == {7}


def test_created_bot_sends_through_rate_limiter():
    bot: Bot = create_bot("123456:TEST")

    assert any(isinstance(m, RateLimiter) for m in bot.session.middleware)
