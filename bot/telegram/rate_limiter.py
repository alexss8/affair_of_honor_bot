"""Лимитер запросов к Telegram: отправки, правки и удаления сообщений ждут свободного слота, а не ловят 429.

Подключается к сессии бота (`bot.session.middleware(RateLimiter())`), поэтому через него идут все запросы —
и из обработчиков, и рассылки при старте и остановке. Лимиты — из FAQ Telegram (https://core.telegram.org/bots/faq),
у группы стартовый запас вдвое; правило везде одно: не больше N запросов за любые T секунд.

Если Telegram всё же ответил 429, лимитер ставит чат на паузу `retry_after` и подстраивает темп: первый 429 в группе
откатывает лимит всех групп к FAQ, следующие понемногу снижают лимит этой группы, без 429 он плавно возвращается.
Выученное живёт только в памяти: после рестарта снова стартовые лимиты.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType

from bot.logger import log_chat

logger = logging.getLogger("duel_bot")

GLOBAL_LIMIT, GLOBAL_PERIOD_SEC = 25, 1.0
# В среднем 1 запрос в секунду, короткий всплеск до 3 подряд (FAQ допускает короткие всплески)
CHAT_LIMIT, CHAT_PERIOD_SEC = 3, 3.0
GROUP_LIMIT, GROUP_PERIOD_SEC = 40, 60.0
GROUP_FALLBACK_LIMIT = 20  # лимит группы из FAQ — после первого 429 в любой группе
GROUP_MIN_LIMIT = 5  # ниже карточка почти перестанет обновляться
GROUP_LOWER_FACTOR = 0.75  # каждый следующий 429 в группе снижает её лимит на четверть
GROUP_RECOVERY_SEC = 60.0  # за каждую такую паузу без 429 лимит группы растёт на 1

# Ограничиваются только запросы, которые создают, меняют или удаляют сообщения в чате
_LIMITED_PREFIXES = ("send", "edit", "delete", "copy", "forward")


class _Window:
    """Время недавних запросов за последние period секунд."""

    def __init__(self, period: float) -> None:
        self.period = period
        self._sent: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._sent and self._sent[0] <= now - self.period:
            self._sent.popleft()

    def wait_time(self, now: float, limit: int) -> float:
        """Сколько ждать, чтобы запрос не стал (limit + 1)-м за period секунд."""
        self._prune(now)
        if len(self._sent) < limit:
            return 0.0
        return self._sent[-limit] + self.period - now

    def record(self, now: float) -> None:
        self._sent.append(now)

    def is_empty(self, now: float) -> bool:
        self._prune(now)
        return not self._sent


@dataclass
class _ChatState:
    is_group: bool
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # очередь запросов чата по порядку
    chat: _Window = field(default_factory=lambda: _Window(CHAT_PERIOD_SEC))
    group: _Window = field(default_factory=lambda: _Window(GROUP_PERIOD_SEC))
    paused_until: float = 0.0
    lowered_limit: int | None = None  # сниженный после 429 лимит группы; None — общий лимит групп
    lowered_at: float = 0.0


def _is_group(chat_id: int | str) -> bool:
    # Отрицательный id — группа или канал; строковый @username считаем группой — так осторожнее
    return isinstance(chat_id, str) or chat_id < 0


class RateLimiter(BaseRequestMiddleware):
    """Middleware сессии aiogram: держит лимиты на весь бот, на чат и на группу и подстраивает их по 429."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.group_limit = GROUP_LIMIT
        self._clock = clock
        self._sleep = sleep
        self._global = _Window(GLOBAL_PERIOD_SEC)
        self._global_paused_until = 0.0
        self._chats: dict[int | str, _ChatState] = {}
        self._last_sweep = clock()

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        chat_id = getattr(method, "chat_id", None)
        if chat_id is None or not method.__api_method__.startswith(_LIMITED_PREFIXES):
            return await make_request(bot, method)
        state = self._chat_state(chat_id)
        async with state.lock:
            await self._wait_slot(state)
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as e:
            self._on_flood(chat_id, state, method.__api_method__, e.retry_after)
            raise  # повтор запроса — дело вызывающего кода (safe_api)

    def group_limit_for(self, chat_id: int | str) -> int:
        """Текущий лимит запросов в минуту для группы с учётом снижения после 429."""
        state = self._chats.get(chat_id)
        return self.group_limit if state is None else self._group_limit(state, self._clock())

    def _group_limit(self, state: _ChatState, now: float) -> int:
        if state.lowered_limit is None:
            return self.group_limit
        limit = state.lowered_limit + int((now - state.lowered_at) // GROUP_RECOVERY_SEC)
        if limit >= self.group_limit:
            state.lowered_limit = None
            return self.group_limit
        return limit

    def _chat_state(self, chat_id: int | str) -> _ChatState:
        now = self._clock()
        if now - self._last_sweep >= GROUP_PERIOD_SEC:
            self._drop_idle_chats(now)
        state = self._chats.get(chat_id)
        if state is None:
            state = self._chats[chat_id] = _ChatState(is_group=_is_group(chat_id))
        return state

    def _drop_idle_chats(self, now: float) -> None:
        self._last_sweep = now
        idle = [
            chat_id
            for chat_id, s in self._chats.items()
            if not s.lock.locked()
            and s.chat.is_empty(now)
            and s.group.is_empty(now)
            and s.paused_until <= now
            and self._group_limit(s, now) == self.group_limit
        ]
        for chat_id in idle:
            del self._chats[chat_id]

    async def _wait_slot(self, state: _ChatState) -> None:
        while True:
            now = self._clock()
            # Лимиты и паузы читаются на каждой проверке: их изменение действует и на уже ждущие запросы
            windows = [(self._global, GLOBAL_LIMIT), (state.chat, CHAT_LIMIT)]
            if state.is_group:
                windows.append((state.group, self._group_limit(state, now)))
            delay = max(
                state.paused_until - now,
                self._global_paused_until - now,
                *(window.wait_time(now, limit) for window, limit in windows),
            )
            if delay <= 0:
                for window, _ in windows:
                    window.record(now)
                return
            await self._sleep(delay)

    def _on_flood(self, chat_id: int | str, state: _ChatState, api_method: str, retry_after: float) -> None:
        now = self._clock()
        state.paused_until = max(state.paused_until, now + retry_after)
        # 429 сразу в двух чатах — похоже на общий лимит: ждут все чаты до самого позднего конца паузы
        paused = [s.paused_until for s in self._chats.values() if s.paused_until > now]
        everyone = len(paused) > 1
        if everyone:
            self._global_paused_until = max(self._global_paused_until, *paused)

        with log_chat(chat_id if isinstance(chat_id, int) else None):
            if state.is_group:
                if self.group_limit > GROUP_FALLBACK_LIMIT:
                    self.group_limit = GROUP_FALLBACK_LIMIT
                    logger.warning(
                        "Лимит групп снижен с %s до %s/мин после ответа 429", GROUP_LIMIT, GROUP_FALLBACK_LIMIT
                    )
                else:
                    current = self._group_limit(state, now)
                    state.lowered_limit = max(GROUP_MIN_LIMIT, int(current * GROUP_LOWER_FACTOR))
                    state.lowered_at = now
            logger.warning(
                "Лимит Telegram (429) на %s: пауза %s с%s, лимит группы %s/мин",
                api_method,
                retry_after,
                " для всех чатов" if everyone else "",
                self._group_limit(state, now) if state.is_group else "-",
            )
