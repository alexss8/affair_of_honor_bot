import asyncio
from dataclasses import dataclass, field
from enum import StrEnum


class Zone(StrEnum):
    TOP = "top"
    BOT = "bot"


class MoveKind(StrEnum):
    ATTACK = "attack"
    DEFEND = "defend"


class DuelPhase(StrEnum):
    """Жизненный цикл боя. Приглашение (до боя) — отдельная сущность DuelInvite."""

    CHOOSING = "choosing"  # игроки выбирают удар/блок
    ROLLING = "rolling"  # оба выбрали, идёт «бросок кубиков» перед расчётом раунда
    FINISHED = "finished"  # бой завершён любым способом; сессия удалена из движка


class OutcomeKind(StrEnum):
    KNOCKOUT = "knockout"  # у защитника кончились жизни
    SURRENDER = "surrender"
    TIMEOUT = "timeout"  # время вышло, победа по оставшимся жизням
    DRAW = "draw"  # время вышло при равных жизнях


class ChoiceResult(StrEnum):
    ACCEPTED = "accepted"  # выбор принят, ждём соперника
    ROUND_READY = "round_ready"  # оба выбрали — раунд переходит в ROLLING
    ROUND_IN_PROGRESS = "round_in_progress"
    MUST_ATTACK = "must_attack"
    MUST_DEFEND = "must_defend"
    ALREADY_CHOSEN = "already_chosen"


@dataclass
class Player:
    id: int
    name: str
    hp: int
    choice: Zone | None = None


@dataclass
class DuelInvite:
    chat_id: int
    message_id: int
    challenger_id: int
    challenger_name: str
    target_id: int
    target_name: str
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class DuelSession:
    chat_id: int
    message_id: int
    p1: Player
    p2: Player
    attacker_id: int
    max_hp: int
    phase: DuelPhase = DuelPhase.CHOOSING
    last_log: str = ""
    surrender_confirm_id: int | None = None
    deadline: float | None = None  # time.time() окончания боя; ставится при запуске таймера
    timeout_task: asyncio.Task | None = field(default=None, repr=False, compare=False)
    # Сериализует правки карточки боя: правка, начатая раньше, не перезапишет более свежее состояние.
    card_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def is_active(self) -> bool:
        return self.phase is not DuelPhase.FINISHED

    @property
    def attacker(self) -> Player:
        return self.p1 if self.attacker_id == self.p1.id else self.p2

    @property
    def defender(self) -> Player:
        return self.p2 if self.attacker_id == self.p1.id else self.p1

    def get_player(self, user_id: int) -> Player | None:
        if user_id == self.p1.id:
            return self.p1
        if user_id == self.p2.id:
            return self.p2
        return None

    def get_opponent(self, user_id: int) -> Player:
        return self.p2 if user_id == self.p1.id else self.p1


@dataclass(frozen=True)
class DuelOutcome:
    """Итог боя. winner/loser равны None только при ничьей."""

    kind: OutcomeKind
    session: DuelSession
    winner: Player | None = None
    loser: Player | None = None

    @property
    def is_rated(self) -> bool:
        return self.winner is not None


@dataclass
class PlayerStats:
    nickname: str
    wins: int
    losses: int

    @property
    def winrate(self) -> int:
        total = self.wins + self.losses
        return round((self.wins / total) * 100) if total > 0 else 0
