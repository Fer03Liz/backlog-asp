"""Fechas y días hábiles de Colombia."""
import os
from datetime import date, timedelta
from functools import lru_cache


def today() -> date:
    """Fecha de hoy. BACKLOG_HOY solo existe para pruebas."""
    forced = os.environ.get("BACKLOG_HOY")
    return date.fromisoformat(forced) if forced else date.today()


def d(value):
    return date.fromisoformat(value[:10]) if value else None


def iso(value):
    return value.isoformat() if value else None


def fmt(x: date) -> str:
    return x.strftime("%d/%m/%Y") if x else "—"


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    dd, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - dd - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _next_monday(x: date) -> date:
    return x + timedelta(days=(7 - x.weekday()) % 7)


@lru_cache(maxsize=None)
def holidays_co(year: int) -> frozenset:
    """Festivos de Colombia (Ley 51 de 1983, 'Ley Emiliani')."""
    fixed = [date(year, 1, 1), date(year, 5, 1), date(year, 7, 20), date(year, 8, 7),
             date(year, 12, 8), date(year, 12, 25)]
    movable = [date(year, 1, 6), date(year, 3, 19), date(year, 6, 29), date(year, 8, 15),
               date(year, 10, 12), date(year, 11, 1), date(year, 11, 11)]
    e = _easter(year)
    easter_based = [e - timedelta(days=3), e - timedelta(days=2),          # jueves y viernes santo
                    e + timedelta(days=43), e + timedelta(days=64), e + timedelta(days=71)]
    return frozenset(fixed) | frozenset(_next_monday(x) for x in movable) | frozenset(easter_based)


class Calendar:
    def __init__(self, extra=()):
        self.extra = {d(x) if isinstance(x, str) else x for x in extra}

    def is_holiday(self, x: date) -> bool:
        return x in holidays_co(x.year) or x in self.extra

    def is_business(self, x: date) -> bool:
        return x.weekday() < 5 and not self.is_holiday(x)

    def next_business_after(self, x: date) -> date:
        y = x + timedelta(days=1)
        while not self.is_business(y):
            y += timedelta(days=1)
        return y

    def first_business_from(self, x: date) -> date:
        return x if self.is_business(x) else self.next_business_after(x)

    def prev_business_before(self, x: date) -> date:
        y = x - timedelta(days=1)
        while not self.is_business(y):
            y -= timedelta(days=1)
        return y

    def business_days_between(self, a: date, b: date) -> int:
        """Días hábiles en el intervalo (a, b]."""
        if not a or not b or b <= a:
            return 0
        n, x = 0, a
        while x < b:
            x += timedelta(days=1)
            n += self.is_business(x)
        return n

    def add_business_days(self, x: date, n: int) -> date:
        for _ in range(n):
            x = self.next_business_after(x)
        return x
