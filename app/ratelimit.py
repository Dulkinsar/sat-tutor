"""Sliding-window rate limits for the public demo. The visitor is the Nth address from the
right in X-Forwarded-For, N = TRUSTED_PROXY_HOPS.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import OrderedDict, deque

from starlette.requests import Request

HOUR = 3600
DAY = 86400


def trusted_hops() -> int:
    value = os.getenv("TRUSTED_PROXY_HOPS")
    if value is not None:
        return int(value)
    return 1 if (os.getenv("SPACE_ID") or os.getenv("RENDER")) else 0


def _bucket(address: str) -> str:
    # one IPv6 subscriber usually owns a whole /64, so key on that
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def visitor_key(request: Request) -> str:
    hops = trusted_hops()
    peer = request.client.host if request.client else "unknown"
    if hops <= 0:
        return _bucket(peer)
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    chain.append(peer)
    return _bucket(chain[-hops - 1] if len(chain) > hops else chain[0])


class RateLimiter:
    def __init__(self, max_keys: int = 10_000) -> None:
        self._lock = threading.Lock()
        self._windows: OrderedDict[str, deque[float]] = OrderedDict()
        self._pinned: dict[str, deque[float]] = {}  # global windows, never evicted
        self._max_keys = max_keys

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()
            self._pinned.clear()

    def hit(self, key: str, limit: int, window: float, now: float | None = None, pinned: bool = False) -> bool:
        """Record a hit if `key` is under `limit` within `window` seconds; return whether it was allowed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if pinned:
                hits = self._pinned.setdefault(key, deque())
            else:
                hits = self._windows.get(key)
                if hits is None:
                    hits = self._windows[key] = deque()
                self._windows.move_to_end(key)
            while hits and hits[0] <= now - window:
                hits.popleft()
            allowed = len(hits) < limit
            if allowed:
                hits.append(now)
            while len(self._windows) > self._max_keys:
                self._windows.popitem(last=False)
            return allowed

    def __len__(self) -> int:
        return len(self._windows)

    def __contains__(self, key: str) -> bool:
        return key in self._windows

    # The policies used by the API
    def allow_chat(self, visitor: str) -> tuple[bool, str]:
        if not self.hit(f"chat:{visitor}", int(os.getenv("CHAT_LIMIT_PER_HOUR", "30")), HOUR):
            return False, "You've reached this demo's hourly message limit. Please try again later."
        if not self.hit("chat:*", int(os.getenv("CHAT_LIMIT_GLOBAL_PER_DAY", "500")), DAY, pinned=True):
            return False, "The demo has reached today's message limit. Please come back tomorrow."
        return True, ""

    def allow_session(self, visitor: str) -> bool:
        return self.hit(f"session:{visitor}", int(os.getenv("SESSIONS_PER_HOUR", "10")), HOUR)
