import threading
import time


class RateLimiter:
    """Limita a N requisições/segundo (thread-safe). Evita estourar a cota da API."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval
