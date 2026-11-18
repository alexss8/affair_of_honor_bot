"""Лимитер запросов к Telegram на подменённых часах: время сдвигает фейковый sleep, реальных пауз нет."""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetChat, GetUpdates, SendMessage, SetMyCommands
from helpers import FakeClock, max_in_window

from bot.__main__ import create_bot
from bot.logger import make_formatter
from bot.telegram.rate_limiter import (
    CHAT_LIMIT,
    CHAT_PERIOD_SEC,
    GLOBAL_LIMIT,
    GLOBAL_PERIOD_SEC,
    GROUP_FALLBACK_LIMIT,
    GROUP_LIMIT,
    GROUP_MIN_LIMIT,
    GROUP_PERIOD_SEC,
    RateLimiter,
    find_rate_limiter,
)
from bot.telegram.safe_api import safe_edit_text

GROUP = -100123
OTHER_GROUP = -100456


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
    limiter = RateLimiter()
    bot: Bot = create_bot("123456:TEST", limiter)

    assert find_rate_limiter(bot) is limiter


# --- 5.4: обратная связь по 429 ---


def flooding_sender(clock: FakeClock, log: list, floods: dict):
    """Заглушка сети: для чатов из floods очередной запрос отвечает 429 с указанным retry_after."""

    async def make_request(bot, method):
        pending = floods.get(method.chat_id)
        if pending:
            raise TelegramRetryAfter(method=method, message="Flood control exceeded", retry_after=pending.pop(0))
        log.append((clock.now, method))
        return True

    return make_request


async def call_ignoring_flood(limiter, request, method) -> None:
    """Запрос через лимитер; ответ 429 пробрасывается — здесь его проглатываем, как safe_api после повтора."""
    with contextlib.suppress(TelegramRetryAfter):
        await limiter(request, None, method)


async def test_flood_pauses_only_that_chat_and_reraises(limiter, clock):
    log: list = []
    request = flooding_sender(clock, log, {GROUP: [5]})

    with pytest.raises(TelegramRetryAfter):
        await limiter(request, None, edit(GROUP, 1))
    flood_at = clock.now
    await limiter(request, None, edit(OTHER_GROUP))
    await limiter(request, None, edit(GROUP, 2))

    sent = {m.chat_id: t for t, m in log}
    assert sent[OTHER_GROUP] == flood_at
    assert sent[GROUP] >= flood_at + 5


async def test_queued_requests_wait_for_pause(limiter, clock):
    log: list = []
    request = flooding_sender(clock, log, {GROUP: [5]})

    await asyncio.gather(*(call_ignoring_flood(limiter, request, edit(GROUP, i)) for i in range(4)))

    assert len(log) == 3
    assert all(t >= 5 for t, _ in log)


async def test_floods_in_two_chats_pause_every_chat(limiter, clock):
    log: list = []
    request = flooding_sender(clock, log, {GROUP: [5], OTHER_GROUP: [8]})

    await call_ignoring_flood(limiter, request, edit(GROUP))
    await call_ignoring_flood(limiter, request, edit(OTHER_GROUP))
    second_flood_at = clock.now
    await limiter(request, None, SendMessage(chat_id=42, text="x"))

    assert log[0][1].chat_id == 42
    assert log[0][0] >= second_flood_at + 8


async def test_first_group_flood_rolls_back_group_limit_once(limiter, clock, caplog):
    request = flooding_sender(clock, [], {GROUP: [1], OTHER_GROUP: [1]})

    with caplog.at_level(logging.WARNING, logger="duel_bot"):
        await call_ignoring_flood(limiter, request, edit(GROUP))
        clock.now += 10
        await call_ignoring_flood(limiter, request, edit(OTHER_GROUP))

    assert limiter.group_limit == GROUP_FALLBACK_LIMIT == 20
    assert sum("Лимит групп снижен" in r.getMessage() for r in caplog.records) == 1


async def test_private_chat_flood_keeps_group_limit(limiter, clock):
    await call_ignoring_flood(limiter, flooding_sender(clock, [], {42: [1]}), SendMessage(chat_id=42, text="x"))

    assert limiter.group_limit == GROUP_LIMIT


async def test_repeated_floods_lower_only_that_group(limiter, clock):
    request = flooding_sender(clock, [], {GROUP: [1] * 12})

    limits = []
    for _ in range(3):
        await call_ignoring_flood(limiter, request, edit(GROUP))
        limits.append(limiter.group_limit_for(GROUP))
    assert limits == [20, 15, 11]
    assert limiter.group_limit_for(OTHER_GROUP) == 20

    for _ in range(9):
        await call_ignoring_flood(limiter, request, edit(GROUP))
    assert limiter.group_limit_for(GROUP) == GROUP_MIN_LIMIT == 5


async def test_lowered_group_limit_recovers_but_not_above_fallback(limiter, clock):
    request = flooding_sender(clock, [], {GROUP: [1, 1]})
    await call_ignoring_flood(limiter, request, edit(GROUP))
    await call_ignoring_flood(limiter, request, edit(GROUP))
    assert limiter.group_limit_for(GROUP) == 15

    clock.now += 10 * 60
    await limiter(request, None, edit(GROUP))
    assert limiter.group_limit_for(GROUP) == 20
    clock.now += 10 * 60
    assert limiter.group_limit_for(GROUP) == 20


def in_flight_flooding_sender(clock: FakeClock, floods: dict):
    """Как flooding_sender, но ответ приходит не сразу: запросы всплеска успевают уйти в Telegram вместе."""
    instant = flooding_sender(clock, [], floods)

    async def make_request(bot, method):
        await asyncio.sleep(0)
        return await instant(bot, method)

    return make_request


async def test_floods_from_one_burst_roll_back_group_limit_once(limiter, clock, caplog):
    request = in_flight_flooding_sender(clock, {GROUP: [5, 5, 5]})

    with caplog.at_level(logging.WARNING, logger="duel_bot"):
        await asyncio.gather(*(call_ignoring_flood(limiter, request, edit(GROUP, i)) for i in range(CHAT_LIMIT)))

    assert limiter.group_limit_for(GROUP) == GROUP_FALLBACK_LIMIT
    messages = [r.getMessage() for r in caplog.records]
    assert sum("Лимит групп снижен" in m for m in messages) == 1
    assert sum("(429)" in m for m in messages) == 1


async def test_flood_from_one_burst_extends_pause_without_lowering_limit(limiter, clock):
    request = in_flight_flooding_sender(clock, {GROUP: [5, 8]})
    await call_ignoring_flood(limiter, flooding_sender(clock, [], {GROUP: [1]}), edit(GROUP))  # лимит уже 20
    clock.now += 2  # пауза кончилась

    await asyncio.gather(*(call_ignoring_flood(limiter, request, edit(GROUP, i)) for i in range(2)))

    assert limiter.group_limit_for(GROUP) == 15  # один шаг снижения на всплеск, а не два
    assert limiter.estimate_wait(GROUP) == pytest.approx(8)


async def test_flood_during_pause_of_every_chat_does_not_lower_limit(limiter, clock):
    third = -100789
    request = in_flight_flooding_sender(clock, {GROUP: [5], OTHER_GROUP: [8], third: [5]})

    await asyncio.gather(*(call_ignoring_flood(limiter, request, edit(chat)) for chat in (GROUP, OTHER_GROUP, third)))

    # 429 в двух группах поставили общую паузу; ответ третьей группе пришёл уже во время неё
    assert limiter.group_limit_for(third) == GROUP_FALLBACK_LIMIT
    assert limiter.estimate_wait(third) == pytest.approx(8)


async def test_flood_writes_one_log_record_with_method_and_chat(limiter, clock, caplog):
    caplog.handler.setFormatter(make_formatter())
    request = flooding_sender(clock, [], {GROUP: [7]})

    with caplog.at_level(logging.WARNING, logger="duel_bot"):
        await call_ignoring_flood(limiter, request, edit(GROUP))

    pauses = [line for line in caplog.text.splitlines() if "(429)" in line]
    assert len(pauses) == 1
    assert "editMessageText" in pauses[0]
    assert "7 с" in pauses[0]
    assert f"chat={GROUP}" in pauses[0]


async def test_safe_api_does_not_log_flood_itself(caplog):
    msg = MagicMock()
    msg.chat.id = GROUP
    flood = TelegramRetryAfter(method=edit(GROUP), message="Flood control exceeded", retry_after=0.01)
    msg.edit_text = AsyncMock(side_effect=[flood, True])

    with caplog.at_level(logging.WARNING, logger="duel_bot"):
        assert await safe_edit_text(msg, "текст") is True

    assert msg.edit_text.await_count == 2
    assert not [r for r in caplog.records if "429" in r.getMessage()]


# --- 5.7: много одновременных дуэлей ---


async def test_concurrent_duels_stay_within_all_budgets(limiter, clock):
    groups = [-100000 - i for i in range(30)]
    privates = [1000 + i for i in range(5)]
    methods = [edit(g, n) for n in range(45) for g in groups]
    methods += [SendMessage(chat_id=p, text=str(n)) for n in range(10) for p in privates]

    log = await send_all(limiter, clock, methods)

    assert len(log) == len(methods)
    assert max_in_window([t for t, _ in log], GLOBAL_PERIOD_SEC) == GLOBAL_LIMIT  # 30 групп хотят больше 25/с
    for chat_id in groups + privates:
        times = [t for t, m in log if m.chat_id == chat_id]
        assert max_in_window(times, CHAT_PERIOD_SEC) <= CHAT_LIMIT
        if chat_id in groups:  # здесь упирается общий лимит; сам лимит группы — test_group_limit_per_minute
            assert max_in_window(times, GROUP_PERIOD_SEC) <= GROUP_LIMIT


async def test_first_group_flood_throttles_other_groups_in_flight(limiter, clock):
    log: list = []
    request = flooding_sender(clock, log, {GROUP: [1]})
    queued = [asyncio.create_task(limiter(request, None, edit(OTHER_GROUP, i))) for i in range(60)]
    while len(log) <= GROUP_FALLBACK_LIMIT:  # вторая группа уже отправила больше 20 правок
        await asyncio.sleep(0)
    sleeps = clock.sleeps
    while clock.sleeps == sleeps:  # и её следующий запрос уже ждёт слота
        await asyncio.sleep(0)

    await call_ignoring_flood(limiter, request, edit(GROUP))
    sent_before_flood = len(log)
    await asyncio.gather(*queued)

    times = [t for t, _ in log]  # в журнал попадает только вторая группа: запрос первой ответил 429
    assert len(times) == 60
    after_flood = times[sent_before_flood:]
    for t in after_flood:  # каждая отправка после 429 укладывается в 20 за минуту, считая и отправленные до него
        assert sum(1 for u in times if t - GROUP_PERIOD_SEC < u <= t) <= GROUP_FALLBACK_LIMIT


# --- 5.8: оценка ожидания для подсказки игроку ---


async def test_estimate_for_unknown_chat_is_zero(limiter):
    assert limiter.estimate_wait(GROUP) == 0.0


async def test_estimate_for_unknown_chat_includes_pause_of_every_chat(limiter, clock):
    request = flooding_sender(clock, [], {GROUP: [5], OTHER_GROUP: [8]})
    await call_ignoring_flood(limiter, request, edit(GROUP))
    await call_ignoring_flood(limiter, request, edit(OTHER_GROUP))

    assert limiter.estimate_wait(-100789) == pytest.approx(8)
    assert -100789 not in limiter._chats  # оценка не заводит состояние чата


async def test_estimate_counts_down_flood_pause(limiter, clock):
    await call_ignoring_flood(limiter, flooding_sender(clock, [], {GROUP: [10]}), edit(GROUP))

    assert limiter.estimate_wait(GROUP) == pytest.approx(10)
    clock.now += 4
    assert limiter.estimate_wait(GROUP) == pytest.approx(6)


def gated_limiter(clock: FakeClock, gate: asyncio.Event) -> RateLimiter:
    """Лимитер, чьи ожидания стоят, пока закрыт gate: так видно, кто ждёт, до того как время сдвинется."""

    async def sleep(delay: float) -> None:
        await gate.wait()
        await clock.sleep(delay)

    return RateLimiter(clock=clock, sleep=sleep)


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_estimate_matches_actual_send_time_behind_queue(clock):
    gate = asyncio.Event()
    gate.set()
    limiter = gated_limiter(clock, gate)
    limiter.group_limit = 20
    log: list = []
    request = make_sender(clock, log)
    await asyncio.gather(*(limiter(request, None, edit(GROUP, i)) for i in range(20)))  # окно группы заполнено
    gate.clear()
    queued = [asyncio.create_task(limiter(request, None, edit(GROUP, 100 + i))) for i in range(3)]
    await settle()  # запросы встали в очередь чата

    estimated_at, estimate = clock.now, limiter.estimate_wait(GROUP)
    gate.set()
    await limiter(request, None, edit(GROUP, 200))
    await asyncio.gather(*queued)

    sent_at = next(t for t, m in log if m.text == "правка 200")
    assert estimate > 0
    assert estimate == pytest.approx(sent_at - estimated_at)


async def test_estimate_does_not_take_a_slot(limiter, clock):
    log: list = []
    request = make_sender(clock, log)
    for i in range(CHAT_LIMIT - 1):
        await limiter(request, None, edit(GROUP, i))
    sleeps_before = clock.sleeps

    for _ in range(5):
        assert limiter.estimate_wait(GROUP) == 0.0
    await limiter(request, None, edit(GROUP, 99))

    assert clock.sleeps == sleeps_before  # последний свободный слот не заняла оценка


async def test_estimate_forgets_finished_and_cancelled_waiters(clock):
    gate = asyncio.Event()
    limiter = gated_limiter(clock, gate)
    await call_ignoring_flood(limiter, flooding_sender(clock, [], {GROUP: [10]}), edit(GROUP))
    alone = limiter.estimate_wait(GROUP)
    cancelled = [asyncio.create_task(limiter.wait_ready(GROUP)) for _ in range(3)]
    await settle()
    assert limiter.estimate_wait(GROUP) > alone  # ждущие учитываются

    for task in cancelled:
        task.cancel()
    await asyncio.gather(*cancelled, return_exceptions=True)
    assert limiter.estimate_wait(GROUP) == pytest.approx(alone)

    finished = [asyncio.create_task(limiter.wait_ready(GROUP)) for _ in range(3)]
    await settle()
    gate.set()
    await asyncio.gather(*finished)
    assert limiter.estimate_wait(GROUP) == 0.0
