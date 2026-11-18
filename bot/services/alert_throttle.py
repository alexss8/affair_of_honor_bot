"""Ограничение частоты алертов админу: одинаковая ошибка — не чаще раза в ALERT_COOLDOWN_SEC."""

import time
import traceback
from collections.abc import Callable, Hashable

ALERT_COOLDOWN_SEC = 600


def error_key(exc: BaseException) -> tuple[str, str, int]:
    """Ключ «одинаковой» ошибки: тип и место возникновения (файл, строка), без текста.

    Текст часто содержит id и числа, из-за которых одна и та же ошибка выглядела бы как разные.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return type(exc).__qualname__, "", 0
    last = frames[-1]
    return type(exc).__qualname__, last.filename, last.lineno or 0


class AlertThrottle:
    """Пропускает первый алерт по ключу, повторы в течение cooldown_sec только считает."""

    def __init__(self, cooldown_sec: float = ALERT_COOLDOWN_SEC, clock: Callable[[], float] = time.monotonic) -> None:
        self._cooldown_sec = cooldown_sec
        self._clock = clock
        self._last_sent: dict[Hashable, float] = {}
        self._suppressed: dict[Hashable, int] = {}

    def register(self, key: Hashable) -> int | None:
        """Учитывает очередную ошибку с ключом key.

        Returns:
            None — алерт не отправлять; иначе отправлять, а число — сколько повторов
            было подавлено с прошлого алерта по этому ключу.
        """
        now = self._clock()
        last = self._last_sent.get(key)
        if last is not None and now - last < self._cooldown_sec:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return None
        self._last_sent[key] = now
        return self._suppressed.pop(key, 0)
