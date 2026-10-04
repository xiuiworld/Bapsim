"""Local-time notification window; decisions continue while delivery waits."""

from datetime import datetime, time, timedelta, timezone


KST = timezone(timedelta(hours=9))


class QuietHours:
    def __init__(self, start: str = "22:00", end: str = "08:00") -> None:
        self.configure(start, end)

    def configure(self, start: str, end: str) -> None:
        try:
            first = time.fromisoformat(start)
            last = time.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ValueError("quiet hours require HH:MM") from exc
        if first.tzinfo or last.tzinfo or first.second or last.second or first == last:
            raise ValueError("quiet hours require two distinct minute times")
        self.start, self.end = first.strftime("%H:%M"), last.strftime("%H:%M")
        self._start, self._end = first, last

    def is_quiet(self, at: str) -> bool:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("quiet hour time requires timezone")
        clock = moment.astimezone(KST).time().replace(tzinfo=None)
        if self._start < self._end:
            return self._start <= clock < self._end
        return clock >= self._start or clock < self._end

    def next_delivery_at(self, at: str) -> str:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if not self.is_quiet(at):
            return moment.astimezone(timezone.utc).isoformat()
        local = moment.astimezone(KST)
        day = local.date()
        if self._start > self._end and local.time().replace(tzinfo=None) >= self._start:
            day += timedelta(days=1)
        return datetime.combine(day, self._end, KST).astimezone(timezone.utc).isoformat()
