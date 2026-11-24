"""Login throttle, in memory: a restart empties it (one server process is assumed).

An attempt is counted when it starts and taken back when it succeeds, so many
attempts sent at the same time cannot pass the limit while the first ones are
still being checked.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass

PAIR_LIMIT = 5
CLIENT_LIMIT = 30
WINDOW_SECONDS = 5 * 60
MAX_KEYS = 4096


class TooManyAttempts(Exception):
    """``first`` is true once per lock, so the audit log gets one line and not one per request."""

    def __init__(self, *, first: bool) -> None:
        super().__init__("too many attempts")
        self.first = first


class _Counter:
    """Attempt times per key within the window; the least recently used key goes first."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._times: OrderedDict[Hashable, deque[float]] = OrderedDict()
        self._reported: set[Hashable] = set()

    def _forget(self, key: Hashable) -> None:
        self._times.pop(key, None)
        self._reported.discard(key)

    def is_full(self, key: Hashable, now: float) -> bool:
        times = self._times.get(key)
        if times is None:
            return False
        while times and now - times[0] >= WINDOW_SECONDS:
            times.popleft()
        if not times:
            self._forget(key)
            return False
        if len(times) < self.limit:
            self._reported.discard(key)
            return False
        return True

    def report(self, key: Hashable) -> bool:
        if key in self._reported:
            return False
        self._reported.add(key)
        return True

    def add(self, key: Hashable, now: float) -> None:
        self._times.setdefault(key, deque()).append(now)
        self._times.move_to_end(key)
        while len(self._times) > MAX_KEYS:
            self._forget(next(iter(self._times)))

    def take_back(self, key: Hashable, at: float) -> None:
        times = self._times.get(key)
        if times is None or at not in times:
            return
        times.remove(at)
        if not times:
            self._forget(key)

    def clear(self, key: Hashable) -> None:
        self._forget(key)


@dataclass(frozen=True)
class Attempt:
    username: str
    client: str
    at: float


class LoginThrottle:
    """5 failures per (username, client), 30 per client, in five minutes."""

    def __init__(self, now: Callable[[], float]) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._pairs = _Counter(PAIR_LIMIT)
        self._clients = _Counter(CLIENT_LIMIT)

    def begin(self, username: str, client: str) -> Attempt:
        with self._lock:
            now = self._now()
            pair = (username, client)
            for counter, key in ((self._clients, client), (self._pairs, pair)):
                if counter.is_full(key, now):
                    raise TooManyAttempts(first=counter.report(key))
            self._pairs.add(pair, now)
            self._clients.add(client, now)
            return Attempt(username, client, now)

    def cancel(self, attempt: Attempt) -> None:
        """The attempt said nothing about the password (the store could not be read)."""
        with self._lock:
            self._pairs.take_back((attempt.username, attempt.client), attempt.at)
            self._clients.take_back(attempt.client, attempt.at)

    def succeed(self, attempt: Attempt) -> None:
        """The pair starts again from zero; failures with other usernames still count for the client."""
        with self._lock:
            self._pairs.clear((attempt.username, attempt.client))
            self._clients.take_back(attempt.client, attempt.at)
