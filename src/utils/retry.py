"""Retry logic with exponential backoff and circuit breaker."""

import asyncio
from functools import wraps
from typing import Callable, TypeVar, Any, Optional
from datetime import datetime, timedelta

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from src.utils.logger import logger


T = TypeVar("T")


# Retry decorators for different services
def retry_claude_api(func: Callable[..., T]) -> Callable[..., T]:
    """Retry decorator for Claude API calls."""
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: logger.warning(
            f"Claude API retry {retry_state.attempt_number}/3: {retry_state.outcome.exception()}"
        ),
    )(func)


def retry_slack_api(func: Callable[..., T]) -> Callable[..., T]:
    """Retry decorator for Slack API calls."""
    return retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=60),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: logger.warning(
            f"Slack API retry {retry_state.attempt_number}/5: {retry_state.outcome.exception()}"
        ),
    )(func)


def retry_moengage_api(func: Callable[..., T]) -> Callable[..., T]:
    """Retry decorator for MoEngage Help Center API calls."""
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: logger.warning(
            f"MoEngage API retry {retry_state.attempt_number}/3: {retry_state.outcome.exception()}"
        ),
    )(func)


class CircuitBreaker:
    """Simple circuit breaker implementation.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failures exceeded threshold, requests fail immediately
    - HALF_OPEN: Testing if service recovered, allow one request
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        name: str = "default"
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        self.failures = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "CLOSED"

    def _should_allow_request(self) -> bool:
        """Check if request should be allowed based on circuit state."""
        if self.state == "CLOSED":
            return True

        if self.state == "OPEN":
            # Check if recovery timeout has passed
            if self.last_failure_time:
                elapsed = datetime.now() - self.last_failure_time
                if elapsed > timedelta(seconds=self.recovery_timeout):
                    self.state = "HALF_OPEN"
                    logger.info(f"Circuit {self.name}: OPEN -> HALF_OPEN")
                    return True
            return False

        # HALF_OPEN: allow one request
        return True

    def record_success(self):
        """Record a successful request."""
        self.failures = 0
        if self.state != "CLOSED":
            logger.info(f"Circuit {self.name}: {self.state} -> CLOSED")
            self.state = "CLOSED"

    def record_failure(self):
        """Record a failed request."""
        self.failures += 1
        self.last_failure_time = datetime.now()

        if self.failures >= self.failure_threshold:
            if self.state != "OPEN":
                logger.warning(
                    f"Circuit {self.name}: {self.state} -> OPEN "
                    f"(failures: {self.failures})"
                )
                self.state = "OPEN"

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator to wrap function with circuit breaker."""

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            if not self._should_allow_request():
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is OPEN"
                )

            try:
                result = await func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                raise

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            if not self._should_allow_request():
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is OPEN"
                )

            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


# Pre-configured circuit breakers
claude_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=60, name="claude")
slack_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=60, name="slack")
moengage_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=60, name="moengage")
