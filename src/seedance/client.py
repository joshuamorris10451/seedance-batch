"""Seedance client for BytePlus ModelArk.

Thin async HTTP client over the documented Ark REST surface. We deliberately do not use
the official SDK at runtime: it pulls a large dependency tree, hides the wire format, and
gives us no room for the reseller fallbacks in providers.py. The parameter set below was
read out of the official SDK source, so it is the real contract, not a guess.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
from typing import Any, Literal, Sequence

import httpx

log = logging.getLogger(__name__)

ARK_INTERNATIONAL = "https://ark.ap-southeast.bytepluses.com/api/v3"
ARK_CHINA = "https://ark.cn-beijing.volces.com/api/v3"

# Model IDs verified present in the live BytePlus ModelArk model list on 2026-07-27.
# The China Ark equivalents use a `doubao-` prefix instead of `dreamina-`.
MODELS = {
    "2.0": "dreamina-seedance-2-0-260128",
    "2.0-fast": "dreamina-seedance-2-0-fast-260128",
    "2.0-mini": "dreamina-seedance-2-0-mini-260615",
    "1.5-pro": "seedance-1-5-pro-251215",
    "1.0-pro": "seedance-1-0-pro-250528",
    "1.0-pro-fast": "seedance-1-0-pro-fast-251015",
}

# Only the full 2.0 model does 1080p/4K; fast and mini top out at 720p. Several
# third-party guides get this wrong and claim 2.0 has no 1080p at all.
MODEL_LIMITS = {
    "dreamina-seedance-2-0-260128": {
        "resolutions": {"480p", "720p", "1080p", "4k"},
        "duration": (4, 15),
    },
    "dreamina-seedance-2-0-fast-260128": {
        "resolutions": {"480p", "720p"},
        "duration": (4, 15),
    },
    "dreamina-seedance-2-0-mini-260615": {
        "resolutions": {"480p", "720p"},
        "duration": (4, 15),
    },
    "seedance-1-5-pro-251215": {
        "resolutions": {"480p", "720p", "1080p"},
        "duration": (4, 12),
    },
}

TERMINAL_OK = "succeeded"
TERMINAL_FAIL = {"failed", "cancelled", "canceled", "expired"}


class SeedanceError(RuntimeError):
    """Any non-retryable failure from the provider."""

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class QuotaExceeded(SeedanceError):
    """The key is out of money or over its rate/concurrency allowance."""


class AuthFailed(SeedanceError):
    """The key is missing, malformed, or revoked."""


@dataclasses.dataclass(slots=True)
class Reference:
    """One reference input attached to a generation.

    `kind` picks the content block type; `role` tells Seedance how to use it. Seedance 2.0
    accepts up to 9 images, 3 videos and 3 audio clips alongside the text prompt.
    """

    url: str
    kind: Literal["image", "video", "audio"] = "image"
    role: str | None = None

    def to_content(self) -> dict[str, Any]:
        key = f"{self.kind}_url"
        block: dict[str, Any] = {"type": key, key: {"url": self.url}}
        block["role"] = self.role or f"reference_{self.kind}"
        return block


@dataclasses.dataclass(slots=True)
class VideoJob:
    """A single generation request, independent of which provider runs it."""

    prompt: str
    model: str = MODELS["2.0"]
    duration: int = 5
    ratio: str = "9:16"
    resolution: str | None = "720p"
    generate_audio: bool = True
    watermark: bool = False
    camera_fixed: bool = False
    seed: int | None = None
    references: Sequence[Reference] = ()
    return_last_frame: bool = False
    callback_url: str | None = None
    # Free-form label so callers can trace a result back to their own row/spreadsheet.
    tag: str | None = None

    def validate(self) -> None:
        limits = MODEL_LIMITS.get(self.model)
        if limits:
            lo, hi = limits["duration"]
            if not lo <= self.duration <= hi:
                raise ValueError(
                    f"{self.model} supports {lo}-{hi}s, got {self.duration}s"
                )
            if self.resolution and self.resolution.lower() not in limits["resolutions"]:
                allowed = ", ".join(sorted(limits["resolutions"]))
                raise ValueError(
                    f"{self.model} supports {allowed}; got {self.resolution}. "
                    "The fast and mini variants are 720p-max — use the full 2.0 model for 1080p/4K."
                )
        images = sum(1 for r in self.references if r.kind == "image")
        videos = sum(1 for r in self.references if r.kind == "video")
        audios = sum(1 for r in self.references if r.kind == "audio")
        if images > 9 or videos > 3 or audios > 3:
            raise ValueError(
                f"Seedance 2.0 accepts <=9 images, <=3 videos, <=3 audio; "
                f"got {images}/{videos}/{audios}"
            )

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        content: list[dict[str, Any]] = [{"type": "text", "text": self.prompt}]
        content.extend(r.to_content() for r in self.references)

        payload: dict[str, Any] = {
            "model": self.model,
            "content": content,
            "duration": self.duration,
            "ratio": self.ratio,
            "generate_audio": self.generate_audio,
            "watermark": self.watermark,
        }
        if self.resolution:
            payload["resolution"] = self.resolution
        if self.camera_fixed:
            payload["camera_fixed"] = True
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.return_last_frame:
            payload["return_last_frame"] = True
        if self.callback_url:
            payload["callback_url"] = self.callback_url
        return payload


@dataclasses.dataclass(slots=True)
class VideoResult:
    task_id: str
    status: str
    video_url: str | None = None
    last_frame_url: str | None = None
    error: str | None = None
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)
    # Populated by the runner so cost reporting is measured, never estimated.
    usage: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == TERMINAL_OK and bool(self.video_url)


class SeedanceClient:
    """One API key, one client. Pooling across keys lives in pool.py."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = ARK_INTERNATIONAL,
        timeout: float = 60.0,
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key:
            raise AuthFailed("empty API key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "SeedanceClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.request(method, url, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    break
                await self._backoff(attempt)
                continue

            if resp.status_code < 300:
                return resp.json() if resp.content else {}

            body = self._safe_json(resp)
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code") or str(resp.status_code)
            message = err.get("message") or resp.text[:400]

            if resp.status_code in (401, 403):
                raise AuthFailed(message, code=code, status=resp.status_code)
            if resp.status_code == 429 or code in {
                "QuotaExceeded",
                "RateLimitExceeded",
                "AccountOverdueError",
                "ServingResourceExhausted",
            }:
                # 429 is retryable; a hard quota/billing error is not.
                if code in {"QuotaExceeded", "AccountOverdueError"}:
                    raise QuotaExceeded(message, code=code, status=resp.status_code)
                if attempt == self.max_retries:
                    raise QuotaExceeded(message, code=code, status=resp.status_code)
                await self._backoff(attempt, resp)
                continue
            if resp.status_code >= 500:
                if attempt == self.max_retries:
                    raise SeedanceError(message, code=code, status=resp.status_code)
                await self._backoff(attempt, resp)
                continue

            raise SeedanceError(message, code=code, status=resp.status_code)

        raise SeedanceError(f"transport failure after {self.max_retries} retries: {last_exc}")

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except Exception:
            return {}

    async def _backoff(self, attempt: int, resp: httpx.Response | None = None) -> None:
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    await asyncio.sleep(min(float(retry_after), 60.0))
                    return
                except ValueError:
                    pass
        # Full jitter, capped. Concurrency limits here are low (3 for individual keys),
        # so being polite on retry matters more than being fast.
        delay = min(2**attempt, 30) * random.random()
        await asyncio.sleep(delay + 0.25)

    async def submit(self, job: VideoJob) -> str:
        data = await self._request(
            "POST", "/contents/generations/tasks", json=job.to_payload()
        )
        task_id = data.get("id")
        if not task_id:
            raise SeedanceError(f"no task id in response: {data}")
        return task_id

    async def fetch(self, task_id: str) -> VideoResult:
        data = await self._request("GET", f"/contents/generations/tasks/{task_id}")
        content = data.get("content") or {}
        error = data.get("error")
        return VideoResult(
            task_id=task_id,
            status=data.get("status", "unknown"),
            video_url=content.get("video_url"),
            last_frame_url=content.get("last_frame_url"),
            error=(error or {}).get("message") if isinstance(error, dict) else error,
            raw=data,
            usage=data.get("usage") or {},
        )

    async def wait(
        self,
        task_id: str,
        *,
        poll_interval: float = 6.0,
        max_wait: float = 900.0,
    ) -> VideoResult:
        waited = 0.0
        interval = poll_interval
        while waited < max_wait:
            result = await self.fetch(task_id)
            if result.status == TERMINAL_OK or result.status in TERMINAL_FAIL:
                return result
            await asyncio.sleep(interval)
            waited += interval
            # Ease off on long jobs, but cap the interval at 8s. A finished task still
            # occupies one of the key's 3 concurrency slots until we notice it, so a long
            # poll interval is throughput you are throwing away: at the old 20s cap a 90s
            # generation was detected 17.6s late — 19.5% of the slot wasted. At 8s the
            # worst case is 4.6%, and polling 3 in-flight tasks every 8s is 22 req/min
            # against an individual key's 180 RPM allowance. Measured in tests/scale_test.py.
            interval = min(interval * 1.25, 8.0)
        raise SeedanceError(f"task {task_id} did not finish within {max_wait}s")

    async def generate(self, job: VideoJob, **wait_kw: Any) -> VideoResult:
        return await self.wait(await self.submit(job), **wait_kw)

    async def cancel(self, task_id: str) -> None:
        await self._request("DELETE", f"/contents/generations/tasks/{task_id}")
