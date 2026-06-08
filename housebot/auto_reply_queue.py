import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from housebot import config
from housebot import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoReplyJob:
    chat_id: int
    source: str
    listing_id: str
    title: str
    runner: Callable[[], Awaitable[bool]]
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.listing_id)


class AutoReplyQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[AutoReplyJob] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        self._pending_keys: set[tuple[str, str]] = set()
        self._current_jobs: dict[str, AutoReplyJob] = {}
        self.enqueued_count = 0
        self.completed_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.last_error = ""

    def start(self) -> None:
        self._worker_tasks = [task for task in self._worker_tasks if not task.done()]
        target_count = max(1, config.AUTO_REPLY_QUEUE_WORKERS)
        if len(self._worker_tasks) >= target_count:
            return
        for index in range(len(self._worker_tasks), target_count):
            worker_name = f"auto-reply-worker-{index + 1}"
            self._worker_tasks.append(asyncio.create_task(self._worker(worker_name), name=worker_name))
        logger.info("Auto-reply queue workers running: %d.", len(self._worker_tasks))

    async def stop(self) -> None:
        if not self._worker_tasks:
            return
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []
        self._current_jobs.clear()

    async def enqueue(self, job: AutoReplyJob) -> bool:
        self.start()
        if job.key in self._pending_keys:
            await db.log_event(
                "auto_reply_queue_duplicate",
                chat_id=job.chat_id,
                source=job.source,
                listing_id=job.listing_id,
                title=job.title,
                status="skipped",
            )
            return False
        self._pending_keys.add(job.key)
        await self._queue.put(job)
        self.enqueued_count += 1
        await db.log_event(
            "auto_reply_queued",
            chat_id=job.chat_id,
            source=job.source,
            listing_id=job.listing_id,
            title=job.title,
            status="queued",
            data={"queue_size": self._queue.qsize()},
        )
        return True

    def snapshot(self) -> dict:
        running_jobs = [
            {
                "worker": worker_name,
                "source": job.source,
                "listing_id": job.listing_id,
                "title": job.title,
                "age_seconds": _elapsed_seconds(job.enqueued_at),
            }
            for worker_name, job in sorted(self._current_jobs.items())
        ]
        return {
            "queued": self._queue.qsize(),
            "running": bool(running_jobs),
            "current": running_jobs[0] if running_jobs else None,
            "running_jobs": running_jobs,
            "workers": len(self._worker_tasks),
            "enqueued": self.enqueued_count,
            "completed": self.completed_count,
            "skipped": self.skipped_count,
            "failed": self.failed_count,
            "last_error": self.last_error,
        }

    async def _worker(self, worker_name: str) -> None:
        while True:
            job = await self._queue.get()
            self._current_jobs[worker_name] = job
            try:
                await db.log_event(
                    "auto_reply_worker_started",
                    chat_id=job.chat_id,
                    source=job.source,
                    listing_id=job.listing_id,
                    title=job.title,
                    status="started",
                    data={"queue_age_seconds": _elapsed_seconds(job.enqueued_at), "worker": worker_name},
                )
                attempted = await job.runner()
                if attempted:
                    self.completed_count += 1
                else:
                    self.skipped_count += 1
                await db.log_event(
                    "auto_reply_worker_finished",
                    chat_id=job.chat_id,
                    source=job.source,
                    listing_id=job.listing_id,
                    title=job.title,
                    status="finished" if attempted else "skipped",
                    data={"queue_age_seconds": _elapsed_seconds(job.enqueued_at), "worker": worker_name},
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failed_count += 1
                self.last_error = str(exc)
                logger.exception("Auto-reply queue job failed for %s:%s", job.source, job.listing_id)
                await db.log_event(
                    "auto_reply_worker_failed",
                    level="error",
                    chat_id=job.chat_id,
                    source=job.source,
                    listing_id=job.listing_id,
                    title=job.title,
                    status="error",
                    detail=str(exc),
                    data={"queue_age_seconds": _elapsed_seconds(job.enqueued_at), "worker": worker_name},
                )
            finally:
                self._pending_keys.discard(job.key)
                self._current_jobs.pop(worker_name, None)
                self._queue.task_done()


def _elapsed_seconds(start: datetime) -> float:
    return round((datetime.now(timezone.utc) - start).total_seconds(), 3)


AUTO_REPLY_QUEUE = AutoReplyQueue()
