"""Process-owned execution policy and neutral client presence.

The Runtime observes admission and termination only. HTTP clients and the
transitional browser adapter register presence here, outside domain execution.
"""
from collections.abc import Callable
import math
from typing import Literal


class Lifetime:
    def __init__(self, mode: Literal['explicit', 'client_presence'], *, clock: Callable[[], float],
                 wall_clock: Callable[[], float], lease: float = 6, grace: float = 15) -> None:
        if not all(math.isfinite(value) and value > 0 for value in (lease, grace)):
            raise ValueError('Lifetime durations must be finite and positive')
        self.mode = mode
        self.lease_seconds = lease
        self.grace_seconds = grace
        self.renew_after_seconds = min(2.0, lease / 3)
        if not 0 < self.renew_after_seconds < lease:
            raise ValueError('Renew interval must be positive and less than lease')
        self._clock, self._wall_clock = clock, wall_clock
        self._sessions: dict[str, float] = {}
        self._idle_since: float | None = clock()
        self._previous_tick, self._previous_wall = clock(), wall_clock()

    def _expire(self) -> None:
        now, wall = self._clock(), self._wall_clock()
        last_expiry = max(self._sessions.values(), default=now)
        if max(now - self._previous_tick, wall - self._previous_wall) > self.lease_seconds:
            self._sessions.clear()
        self._previous_tick, self._previous_wall = now, wall
        self._sessions = {identity: expiry for identity, expiry in self._sessions.items() if expiry > now}
        if self._sessions:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = min(now, last_expiry)

    @property
    def active_clients(self) -> int:
        self._expire()
        return len(self._sessions)

    def admitted(self) -> bool:
        return self.mode == 'explicit' or self.active_clients > 0

    def should_stop(self) -> bool:
        self._expire()
        return self.mode == 'client_presence' and self._idle_since is not None and self._clock() - self._idle_since >= self.grace_seconds

    def register(self, identity: str) -> None:
        self._expire()
        self._sessions[identity] = self._clock() + self.lease_seconds
        self._idle_since = None

    def remaining(self, identity: str) -> float:
        self._expire()
        return max(0, self._sessions.get(identity, self._clock()) - self._clock())

    def renew(self, identity: str) -> bool:
        self._expire()
        if identity not in self._sessions:
            return False
        self._sessions[identity] = self._clock() + self.lease_seconds
        return True

    def close(self, identity: str) -> None:
        self._expire()
        self._sessions.pop(identity, None)
        if not self._sessions and self._idle_since is None:
            self._idle_since = self._clock()
