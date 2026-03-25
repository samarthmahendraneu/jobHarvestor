import time
import threading
from urllib.parse import urlparse
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class DomainRateLimiter:
    """
    Per-domain rate limiter using a token bucket algorithm.
    Ensures we don't hammer any single career site with too many
    concurrent requests, preventing IP bans and throttling.
    """

    def __init__(self, requests_per_minute: int = 10, burst: int = 3):
        self.interval = 60.0 / requests_per_minute  # seconds between requests
        self.burst = burst
        self._last_request: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def _get_domain(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc.lower()

    async def wait(self, url: str):
        """
        Async-compatible wait that blocks until the domain's rate limit allows
        the next request. Uses a simple sliding window per domain.
        """
        import asyncio
        domain = self._get_domain(url)

        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_request[domain]

                if elapsed >= self.interval:
                    self._last_request[domain] = now
                    return  # allowed

            # Wait the remaining time before retrying
            sleep_time = self.interval - elapsed
            logger.debug(f"Rate limiting {domain}: sleeping {sleep_time:.1f}s")
            await asyncio.sleep(sleep_time)


# Global singleton — importable from anywhere
rate_limiter = DomainRateLimiter(requests_per_minute=10, burst=3)
