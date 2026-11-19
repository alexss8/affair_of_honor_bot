"""Трассировка запросов отладочного стенда: строка в лог на каждый запрос, с исходом, ожиданием и текстом."""

import logging

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from helpers import FakeClock

from bot.__main__ import create_bot
from bot.telegram.rate_limiter import RateLimiter, find_rate_limiter
from bot.telegram.request_tracer import TEXT_PREVIEW_LEN, RequestTracer

GROUP = -1001


@pytest.fixture
def trace(caplog):
    caplog.set_level(logging.INFO, logger="duel_bot")
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    return RequestTracer(limiter, clock=clock), clock, caplog


def _records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Запрос ")]


async def test_successful_edit_is_traced_with_text_and_duration(trace):
    tracer, clock, caplog = trace

    async def network(bot, method):
        clock.now += 0.25
        return True

    await tracer(network, None, EditMessageText(chat_id=GROUP, message_id=1, text="🔴 Атакует:\n<b>@alice</b>"))

    assert _records(caplog) == ["Запрос editMessageText: ок за 0.25 с (очередь ~0.0 с) «🔴 Атакует: <b>@alice</b>»"]


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (lambda m: TelegramRetryAfter(method=m, message="Flood", retry_after=7), "429, retry_after=7"),
        (lambda m: TelegramBadRequest(method=m, message="message is not modified"), "message is not modified"),
    ],
)
async def test_failed_request_is_traced_and_reraised(trace, error, outcome):
    tracer, _, caplog = trace
    method = EditMessageText(chat_id=GROUP, message_id=1, text="x")

    async def network(bot, m):
        raise error(m)

    with pytest.raises(type(error(method))):
        await tracer(network, None, method)

    [record] = _records(caplog)
    assert outcome in record


async def test_request_without_chat_and_long_text(trace):
    tracer, _, caplog = trace

    async def network(bot, method):
        return True

    await tracer(network, None, AnswerCallbackQuery(callback_query_id="1", text="Я" * (TEXT_PREVIEW_LEN + 5)))

    [record] = _records(caplog)
    assert record.startswith("Запрос answerCallbackQuery: ок")
    assert record.endswith("Я" * TEXT_PREVIEW_LEN + "…»")


def test_tracer_wraps_limiter_only_in_trace_mode():
    limiter = RateLimiter()
    plain: Bot = create_bot("123456:TEST", limiter)
    traced: Bot = create_bot("123456:TEST", limiter, trace=True)

    assert not any(isinstance(m, RequestTracer) for m in plain.session.middleware)
    assert [type(m) for m in traced.session.middleware] == [RequestTracer, RateLimiter]  # снаружи лимитера
    assert find_rate_limiter(traced) is limiter
