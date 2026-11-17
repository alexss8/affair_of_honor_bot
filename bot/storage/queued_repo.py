import asyncio
import logging

from bot.domain.models import Player, PlayerStats
from bot.storage.base import BaseRepository


class QueuedRepository:
    """KISS-обертка над репозиторием: выносит частые фоновые записи в asyncio.Queue с батчингом."""

    def __init__(self, target_repo: BaseRepository, logger: logging.Logger):
        self.repo = target_repo
        self.logger = logger
        self.queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def init(self) -> None:
        self.repo.init()

    def start_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._process_queue())

    async def stop_worker(self) -> None:
        if self._worker_task:
            await self.queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def save_user(self, chat_id: int, user_id: int, username: str | None, first_name: str) -> None:
        self.queue.put_nowait(("save_user", (chat_id, user_id, username, first_name)))

    def save_users_batch(self, users: list[tuple[int, int, str | None, str]]) -> None:
        self.repo.save_users_batch(users)

    def get_user_by_username(self, chat_id: int, username: str) -> tuple[int, str] | None:
        return self.repo.get_user_by_username(chat_id, username)

    def get_user_names(self, chat_id: int, user_id: int) -> tuple[str | None, str] | None:
        return self.repo.get_user_names(chat_id, user_id)

    def record_duel(self, chat_id: int, winner: Player, loser: Player) -> int:
        return self.repo.record_duel(chat_id, winner, loser)

    def get_top(self, chat_id: int, limit: int = 10) -> list[PlayerStats]:
        return self.repo.get_top(chat_id, limit)

    def get_health_stats(self) -> dict:
        stats = self.repo.get_health_stats()
        stats["queue_size"] = self.queue.qsize()
        return stats

    async def _process_queue(self) -> None:
        while True:
            first_item = await self.queue.get()
            batch = [first_item]

            while not self.queue.empty() and len(batch) < 200:
                batch.append(self.queue.get_nowait())

            users_to_save = [args for task_type, args in batch if task_type == "save_user"]
            try:
                if users_to_save:
                    self.repo.save_users_batch(users_to_save)
            except Exception as e:
                self.logger.error(f"Ошибка фоновой пакетной записи в БД: {e}")
            finally:
                for _ in batch:
                    self.queue.task_done()
