"""
A fixed wall clock for the proactive tests. The quiet hours ("22-7"), "heute keine Ansagen" and the night
volume of the speakers all look at the hour, so a test that leaves the clock alone passes by day and fails
at night. `frozen(when, *modules)` replaces `dt` in the given modules with a copy of `datetime` whose
now()/today() return `when` (naive local or aware); everything else in datetime stays the real thing.
time.time() is not touched: the tests keep using it for "two hours ago" and the like.
"""
import datetime as _dt
import types
from unittest import mock


def today_at(hour, minute=0):
    """Today at that local time, timezone-aware — a fixed hour without moving the date."""
    return _dt.datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)


def frozen(when, *modules):
    """A started patcher group; call .stop() (or use addCleanup) to undo. `when` is aware or naive local."""
    aware = when if when.tzinfo else when.astimezone()

    class FrozenDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return aware.astimezone(tz) if tz else aware.astimezone().replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls.now()

    class FrozenDate(_dt.date):
        @classmethod
        def today(cls):
            return aware.astimezone().date()

    fake = types.SimpleNamespace(**{k: getattr(_dt, k) for k in dir(_dt) if not k.startswith("__")})
    fake.datetime, fake.date = FrozenDateTime, FrozenDate
    patches = [mock.patch.object(m, "dt", fake) for m in modules]
    for p in patches:
        p.start()
    return types.SimpleNamespace(stop=lambda: [p.stop() for p in reversed(patches)])
