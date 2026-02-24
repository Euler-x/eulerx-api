import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

from app.config import get_settings

settings = get_settings()


class InMemoryRateLimiter:
    def __init__(
        self,
        max_requests: int | None = None,
        window_seconds: int | None = None,
    ):
        self.max_requests = max_requests or settings.rate_limit_requests
        self.window_seconds = window_seconds or settings.rate_limit_window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _clean_old_requests(self, key: str) -> None:
        now = time.time()
        cutoff = now - self.window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

    def check(self, key: str) -> bool:
        self._clean_old_requests(key)
        return len(self._requests[key]) < self.max_requests

    def record(self, key: str) -> None:
        self._requests[key].append(time.time())

    def remaining(self, key: str) -> int:
        self._clean_old_requests(key)
        return max(0, self.max_requests - len(self._requests[key]))


rate_limiter = InMemoryRateLimiter()


async def rate_limit_dependency(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{request.url.path}"

    if not rate_limiter.check(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
            headers={
                "Retry-After": str(rate_limiter.window_seconds),
                "X-RateLimit-Limit": str(rate_limiter.max_requests),
                "X-RateLimit-Remaining": "0",
            },
        )

    rate_limiter.record(key)
