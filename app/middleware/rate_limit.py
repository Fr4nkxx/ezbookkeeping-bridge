import time
from collections import deque

from fastapi import HTTPException


class RateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._events: deque[float] = deque()

    def check(self) -> None:
        now = time.monotonic()
        while self._events and now - self._events[0] > 60.0:
            self._events.popleft()
        if len(self._events) >= self._max:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        self._events.append(now)
