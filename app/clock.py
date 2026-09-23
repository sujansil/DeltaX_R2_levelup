from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance_ms(self, ms: int) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(milliseconds=ms)

    def advance_sec(self, sec: float) -> None:
        self.advance_ms(int(sec * 1000))
