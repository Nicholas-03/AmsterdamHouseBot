import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import db

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
        self._worker_task: asyncio.Task | None = None
        self._pending_keys: set[tuple[str, str]] = set()
        self._current_job: AutoReplyJob | None = None
        self.enqueued_count = 0
        self.completed_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.last_error = ""

    def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker(), name="auto-reply-worker")
        logger.info("Auto-reply queue worker started.")

    async def stop(self) -> None:
        if not self._worker_task or self._worker_task.done():
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass

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
        current = None
        if self._current_job:
            current = {
                "source": self._current_job.source,
                "listing_id": self._current_job.listing_id,
                "title": self._current_job.title,
                "age_seconds": _elapsed_seconds(self._current_job.enqueued_at),
            }
        return {
            "queued": self._queue.qsize(),
            "running": self._current_job is not None,
            "current": current,
            "enqueued": self.enqueued_count,
            "completed": self.completed_count,
            "skipped": self.skipped_count,
            "failed": self.failed_count,
            "last_error": self.last_error,
        }

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            self._current_job = job
            try:
                await db.log_event(
                    "auto_reply_worker_started",
                    chat_id=job.chat_id,
                    source=job.source,
                    listing_id=job.listing_id,
                    title=job.title,
                    status="started",
                    data={"queue_age_seconds": _elapsed_seconds(job.enqueued_at)},
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
                    data={"queue_age_seconds": _elapsed_seconds(job.enqueued_at)},
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
                    data={"queue_age_seconds": _elapsed_seconds(job.enqueued_at)},
                )
            finally:
                self._pending_keys.discard(job.key)
                self._current_job = None
                self._queue.task_done()


def _elapsed_seconds(start: datetime) -> float:
    return round((datetime.now(timezone.utc) - start).total_seconds(), 3)


AUTO_REPLY_QUEUE = AutoReplyQueue()
