from dataclasses import dataclass
from typing import Optional

@dataclass
class Player:
    id: int
    name: str
    hp: int
    choice: Optional[str] = None

@dataclass
class DuelSession:
    chat_id: int
    message_id: int
    p1: Player
    p2: Player
    attacker_id: int
    max_hp: int
    surrender_confirm_id: Optional[int] = None

    @property
    def attacker(self) -> Player:
        return self.p1 if self.attacker_id == self.p1.id else self.p2

    @property
    def defender(self) -> Player:
        return self.p2 if self.attacker_id == self.p1.id else self.p1

    def get_player(self, user_id: int) -> Optional[Player]:
        if user_id == self.p1.id:
            return self.p1
        if user_id == self.p2.id:
            return self.p2
        return None

    def get_opponent(self, user_id: int) -> Player:
        return self.p2 if user_id == self.p1.id else self.p1

@dataclass
class PlayerStats:
    nickname: str
    wins: int
    losses: int

    @property
    def winrate(self) -> int:
        total = self.wins + self.losses
        return round((self.wins / total) * 100) if total > 0 else 0
