"""Credential pool + scheduler.

Why this exists: a BytePlus *individual* key allows 180 RPM but only **3 concurrent**
generation tasks (10 for enterprise). Concurrency, not request rate, is the real ceiling on
batch throughput. So the scheduler tracks concurrency per credential and spreads work across
every credential you hold — your own BytePlus keys plus any reseller keys (fal, Segmind,
WaveSpeed, Replicate).

These are keys you legitimately own. Nothing here creates accounts, and nothing here tries to
look like a different person or device — pooling your own paid keys is ordinary client-side
rate management, which is a different thing entirely from farming free tiers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import logging
import time
from typing import Any, Awaitable, Callable, Iterable, TypeVar

from .client import AuthFailed, QuotaExceeded, SeedanceError

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclasses.dataclass
class Credential:
    """One API key and its live health state."""

    name: str
    api_key: str
    provider: str = "byteplus"
    base_url: str | None = None
    max_concurrency: int = 3
    # Set when the key dies (billing, revoked) so the scheduler stops picking it.
    disabled_reason: str | None = None
    # Monotonic timestamp before which we should not use this key again.
    cooldown_until: float = 0.0

    _sem: asyncio.Semaphore = dataclasses.field(init=False, repr=False, default=None)  # type: ignore[assignment]
    in_flight: int = dataclasses.field(default=0, init=False)
    completed: int = dataclasses.field(default=0, init=False)
    failed: int = dataclasses.field(default=0, init=False)
    spend_tokens: int = dataclasses.field(default=0, init=False)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.max_concurrency)

    @property
    def available(self) -> bool:
        return self.disabled_reason is None and time.monotonic() >= self.cooldown_until

    @property
    def free_slots(self) -> int:
        return max(0, self.max_concurrency - self.in_flight)

    def disable(self, reason: str) -> None:
        log.warning("credential %s disabled: %s", self.name, reason)
        self.disabled_reason = reason

    def cool_down(self, seconds: float) -> None:
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)


class CredentialPool:
    """Picks the least-loaded healthy credential and enforces its concurrency cap."""

    def __init__(self, credentials: Iterable[Credential]):
        self._creds = list(credentials)
        if not self._creds:
            raise ValueError("credential pool is empty — add at least one API key")
        self._cv = asyncio.Condition()
        self._rr = itertools.cycle(range(len(self._creds)))

    @property
    def credentials(self) -> list[Credential]:
        return list(self._creds)

    @property
    def total_concurrency(self) -> int:
        return sum(c.max_concurrency for c in self._creds if c.available)

    def _pick(self, provider: str | None) -> Credential | None:
        candidates = [
            c
            for c in self._creds
            if c.available
            and c.free_slots > 0
            and (provider is None or c.provider == provider)
        ]
        if not candidates:
            return None
        # Least in-flight first; ties broken by round-robin order for fairness.
        return min(candidates, key=lambda c: (c.in_flight, c.completed))

    def _any_alive(self, provider: str | None) -> bool:
        return any(
            c.disabled_reason is None and (provider is None or c.provider == provider)
            for c in self._creds
        )

    async def run(
        self,
        fn: Callable[[Credential], Awaitable[T]],
        *,
        provider: str | None = None,
        max_attempts: int = 3,
    ) -> T:
        """Run `fn` against a healthy credential, retrying on a different key if one dies."""
        attempts = 0
        last_error: Exception | None = None

        while attempts < max_attempts:
            if not self._any_alive(provider):
                raise SeedanceError(
                    f"every credential is disabled ({provider or 'any provider'}); "
                    f"last error: {last_error}"
                )

            async with self._cv:
                cred = self._pick(provider)
                while cred is None:
                    if not self._any_alive(provider):
                        raise SeedanceError("every credential is disabled")
                    await self._cv.wait()
                    cred = self._pick(provider)
                cred.in_flight += 1

            try:
                result = await fn(cred)
            except AuthFailed as exc:
                cred.disable(f"auth failed: {exc}")
                cred.failed += 1
                last_error = exc
                attempts += 1
            except QuotaExceeded as exc:
                # Out of credit or throttled — park it rather than hammering.
                if exc.code in {"QuotaExceeded", "AccountOverdueError"}:
                    cred.disable(f"quota/billing: {exc}")
                else:
                    cred.cool_down(30.0)
                cred.failed += 1
                last_error = exc
                attempts += 1
            except Exception as exc:
                cred.failed += 1
                last_error = exc
                raise
            else:
                cred.completed += 1
                return result
            finally:
                async with self._cv:
                    cred.in_flight -= 1
                    self._cv.notify_all()

        raise SeedanceError(
            f"all {max_attempts} attempts failed across the pool; last error: {last_error}"
        )

    def stats(self) -> dict[str, Any]:
        return {
            "credentials": [
                {
                    "name": c.name,
                    "provider": c.provider,
                    "in_flight": c.in_flight,
                    "completed": c.completed,
                    "failed": c.failed,
                    "concurrency": c.max_concurrency,
                    "status": c.disabled_reason or ("cooling" if not c.available else "ok"),
                }
                for c in self._creds
            ],
            "total_concurrency": self.total_concurrency,
        }
