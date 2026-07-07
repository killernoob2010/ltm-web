import threading
import time
from typing import Any, Callable, Optional


class TTLCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._items[key] = (time.time() + ttl_seconds, value)

    def clear(self, prefix: Optional[str] = None) -> None:
        with self._lock:
            if prefix is None:
                self._items.clear()
                return
            for key in list(self._items):
                if key.startswith(prefix):
                    self._items.pop(key, None)

    def get_or_set(self, key: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        value = loader()
        self.set(key, value, ttl_seconds)
        return value


cache = TTLCache()


def ttl_cached(key: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
    return cache.get_or_set(key, ttl_seconds, loader)


def clear_cache(prefix: Optional[str] = None) -> None:
    cache.clear(prefix)
