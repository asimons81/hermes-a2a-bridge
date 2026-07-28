"""Small TTL cache for verified Agent Card endpoints of named registry peers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class _Profile:
    registry_url: str
    endpoint: str
    cached_at: float


class RegistryProfileCache:
    """Caches verified endpoints only; bearer tokens remain in SQLite, never here."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._profiles: dict[str, _Profile] = {}

    def get(self, name: str, registry_url: str, *, ttl_seconds: float) -> str | None:
        profile = self._profiles.get(name)
        if profile is None or profile.registry_url != registry_url:
            return None
        if self._clock() - profile.cached_at >= max(0.0, ttl_seconds):
            self.invalidate(name)
            return None
        return profile.endpoint

    def put(self, name: str, registry_url: str, endpoint: str) -> None:
        self._profiles[name] = _Profile(registry_url, endpoint, self._clock())

    def invalidate(self, name: str) -> None:
        self._profiles.pop(name, None)

    def clear(self) -> None:
        self._profiles.clear()


registry_profile_cache = RegistryProfileCache()
