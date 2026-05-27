import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config
import db

logger = logging.getLogger(__name__)


@dataclass
class SourceState:
    failures: int = 0
    cooldown_until: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str = ""


class SourceHealthRegistry:
    def __init__(self) -> None:
        self._states: dict[str, SourceState] = {}

    def cooldown_status(self, source: str) -> tuple[bool, int]:
        state = self._states.get(source)
        if not state or not state.cooldown_until:
            return False, 0
        remaining = int((state.cooldown_until - _now()).total_seconds())
        if remaining <= 0:
            state.cooldown_until = None
            return False, 0
        return True, remaining

    async def record_success(self, source: str) -> None:
        state = self._states.get(source)
        if not state:
            return
        was_unhealthy = state.failures or state.cooldown_until
        self._states[source] = SourceState()
        if was_unhealthy:
            await db.log_event(
                "source_recovered",
                source=source,
                status="ok",
            )

    async def record_failure(self, source: str, *, status: str, detail: str = "") -> None:
        state = self._states.setdefault(source, SourceState())
        state.failures += 1
        state.last_failure_at = _now()
        state.last_error = detail or status

        threshold = config.SOURCE_FAILURE_COOLDOWN_THRESHOLD
        cooldown_minutes = config.SOURCE_FAILURE_COOLDOWN_MINUTES
        if not threshold or not cooldown_minutes or state.failures < threshold:
            return

        state.cooldown_until = _now() + timedelta(minutes=cooldown_minutes)
        logger.warning(
            "%s entered cooldown for %d minutes after %d consecutive failures.",
            source,
            cooldown_minutes,
            state.failures,
        )
        await db.log_event(
            "source_cooldown_started",
            level="warning",
            source=source,
            status=status,
            detail=detail,
            data={
                "failures": state.failures,
                "cooldown_minutes": cooldown_minutes,
                "cooldown_until": _format_timestamp(state.cooldown_until),
            },
        )

    def snapshot(self) -> dict[str, dict]:
        result = {}
        for source, state in self._states.items():
            in_cooldown, remaining_seconds = self.cooldown_status(source)
            result[source] = {
                "failures": state.failures,
                "cooldown": in_cooldown,
                "cooldown_remaining_seconds": remaining_seconds,
                "cooldown_until": _format_timestamp(state.cooldown_until),
                "last_failure_at": _format_timestamp(state.last_failure_at),
                "last_error": state.last_error,
            }
        return result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


SOURCE_HEALTH = SourceHealthRegistry()
