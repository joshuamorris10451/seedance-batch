"""Reseller adapters — currently fal.ai.

Why this exists: BytePlus is the cheapest way to *buy* Seedance (fal charges exactly 2× the
official token rate), but it is not the cheapest way to *start*. BytePlus gates its free quota
behind identity verification; fal gives a new account $10 of credit against an email address
and no card. For validating the pipeline end to end, that difference is the whole story.

fal's wire format is not BytePlus's. Three differences matter:

1. **Auth is `Authorization: Key <k>`**, not `Bearer`.
2. **The queue returns its own URLs.** Submitting to `queue.fal.run/<endpoint>` gives back
   `status_url` and `response_url`, and we use those verbatim rather than rebuilding the path.
   That deliberately sidesteps fal's nastiest gotcha: for a nested endpoint like
   `fal-ai/flux/dev` the polling path collapses to `fal-ai/flux/requests/<id>` — the trailing
   segment is dropped — so any path we construct ourselves would be wrong for exactly the
   models we care about.
3. **No token accounting comes back.** BytePlus reports `usage.completion_tokens` and the
   runner records real spend from it. fal reports nothing, so `usage` stays empty rather than
   being filled with an estimate. The tool's rule is that no price is ever invented.

Features BytePlus has and fal does not are rejected loudly at submit time instead of being
silently dropped — a chain that quietly stops chaining would burn credit producing garbage.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .client import (
    TERMINAL_FAIL,
    TERMINAL_OK,
    AuthFailed,
    QuotaExceeded,
    SeedanceError,
    VideoJob,
    VideoResult,
)

log = logging.getLogger(__name__)

FAL_QUEUE = "https://queue.fal.run"

# BytePlus model id -> fal endpoint id. Text-to-video and image-to-video are separate
# endpoints on fal, so the runner picks by whether the job carries an image reference.
FAL_ENDPOINTS: dict[str, dict[str, str]] = {
    "dreamina-seedance-2-0-260128": {
        "text": "bytedance/seedance-2.0/text-to-video",
        "image": "bytedance/seedance-2.0/image-to-video",
        "reference": "bytedance/seedance-2.0/reference-to-video",
    },
    "dreamina-seedance-2-0-fast-260128": {
        "text": "bytedance/seedance-2.0/fast/text-to-video",
        "image": "bytedance/seedance-2.0/fast/image-to-video",
        "reference": "bytedance/seedance-2.0/fast/reference-to-video",
    },
    # Mini is the cheap workhorse on fal: $0.0721/s at 480p output vs $0.3034/s for the
    # flagship at 720p. That is what makes a $10 trial credit stretch far enough to be a
    # real test matrix rather than three clips.
    "dreamina-seedance-2-0-mini-260615": {
        "text": "bytedance/seedance-2.0/mini/text-to-video",
        "image": "bytedance/seedance-2.0/mini/image-to-video",
        "reference": "bytedance/seedance-2.0/mini/reference-to-video",
    },
    "seedance-1-0-pro-250528": {
        "text": "fal-ai/bytedance/seedance/v1/pro/text-to-video",
        "image": "fal-ai/bytedance/seedance/v1/pro/image-to-video",
    },
    "seedance-1-0-pro-fast-251015": {
        "text": "fal-ai/bytedance/seedance/v1/pro/fast/text-to-video",
        "image": "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video",
    },
}

# fal accepts a fixed set of resolutions/ratios; anything else is a 422 after we've queued.
FAL_RESOLUTIONS = {"480p", "720p", "1080p", "4k"}
FAL_RATIOS = {"auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}

# fal queue status values -> our terminal vocabulary.
_FAL_STATUS = {
    "IN_QUEUE": "queued",
    "IN_PROGRESS": "running",
    "COMPLETED": TERMINAL_OK,
}


class FalClient:
    """One fal.ai key. Presents the same submit/fetch/wait surface as SeedanceClient."""

    provider = "fal"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = FAL_QUEUE,
        timeout: float = 60.0,
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key:
            raise AuthFailed("empty fal API key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        # request_id -> the URLs fal handed us. Populated by submit(), read by fetch().
        self._routes: dict[str, dict[str, str]] = {}
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "FalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- payload translation ------------------------------------------------
    @staticmethod
    def endpoint_for(job: VideoJob) -> str:
        routes = FAL_ENDPOINTS.get(job.model)
        if not routes:
            raise SeedanceError(
                f"{job.model} has no fal.ai equivalent. Mapped models: "
                f"{', '.join(sorted(FAL_ENDPOINTS))}"
            )
        images = [r for r in job.references if r.kind == "image"]
        if not images:
            return routes["text"]
        if len(images) > 1 and "reference" in routes:
            return routes["reference"]
        if "image" not in routes:
            raise SeedanceError(f"{job.model} on fal.ai has no image-to-video endpoint")
        return routes["image"]

    @staticmethod
    def to_payload(job: VideoJob) -> dict[str, Any]:
        # Reject rather than silently drop: a caller who asked for a locked camera or a
        # chained last frame and got neither would only find out from the output.
        unsupported = []
        if job.return_last_frame:
            unsupported.append(
                "return_last_frame (so `seedance chain` cannot run on fal — use BytePlus)"
            )
        if job.camera_fixed:
            unsupported.append("camera_fixed")
        if job.watermark:
            unsupported.append("watermark")
        if job.seed is not None:
            unsupported.append("seed (fal returns a seed but does not accept one here)")
        if unsupported:
            raise SeedanceError(
                "fal.ai does not support: " + "; ".join(unsupported)
            )

        job.validate()

        resolution = (job.resolution or "720p").lower()
        if resolution not in FAL_RESOLUTIONS:
            raise SeedanceError(
                f"fal.ai accepts {sorted(FAL_RESOLUTIONS)}; got {job.resolution!r}"
            )
        ratio = job.ratio if job.ratio in FAL_RATIOS else "auto"
        if ratio != job.ratio:
            log.warning("fal.ai has no ratio %s; sending 'auto'", job.ratio)

        payload: dict[str, Any] = {
            "prompt": job.prompt,
            "resolution": resolution,
            # fal types duration as a string enum, unlike BytePlus's integer.
            "duration": str(job.duration),
            "aspect_ratio": ratio,
            "generate_audio": job.generate_audio,
        }

        images = [r for r in job.references if r.kind == "image"]
        if images:
            if len(images) == 1:
                payload["image_url"] = images[0].url
            else:
                payload["reference_image_urls"] = [r.url for r in images]
        return payload

    # -- HTTP ---------------------------------------------------------------
    async def _request(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.request(method, url, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 30) * 0.5 + 0.25)
                continue

            if resp.status_code < 300:
                try:
                    return resp.json() if resp.content else {}
                except Exception:
                    return {}

            try:
                body = resp.json()
            except Exception:
                body = {}
            message = ""
            if isinstance(body, dict):
                detail = body.get("detail") or body.get("error") or body.get("message")
                message = detail if isinstance(detail, str) else str(detail or "")
            message = message or resp.text[:400]

            if resp.status_code in (401, 403):
                raise AuthFailed(message, code="AuthenticationError", status=resp.status_code)
            if resp.status_code in (402,):
                # Out of credit — park the key, do not retry.
                raise QuotaExceeded(message, code="QuotaExceeded", status=resp.status_code)
            if resp.status_code == 429:
                if attempt == self.max_retries:
                    raise QuotaExceeded(
                        message, code="RateLimitExceeded", status=resp.status_code
                    )
                await asyncio.sleep(min(2**attempt, 30) * 0.5 + 0.25)
                continue
            if resp.status_code >= 500:
                if attempt == self.max_retries:
                    raise SeedanceError(message, code="ServerError", status=resp.status_code)
                await asyncio.sleep(min(2**attempt, 30) * 0.5 + 0.25)
                continue
            raise SeedanceError(message, code=str(resp.status_code), status=resp.status_code)

        raise SeedanceError(f"transport failure after {self.max_retries} retries: {last_exc}")

    async def submit(self, job: VideoJob) -> str:
        endpoint = self.endpoint_for(job)
        data = await self._request(
            "POST", f"{self.base_url}/{endpoint}", json=self.to_payload(job)
        )
        request_id = data.get("request_id")
        if not request_id:
            raise SeedanceError(f"no request_id in fal response: {data}")
        # Keep fal's own URLs — see the module docstring on the path-collapsing gotcha.
        self._routes[request_id] = {
            "status": data.get("status_url") or f"{self.base_url}/{endpoint}/requests/{request_id}/status",
            "response": data.get("response_url") or f"{self.base_url}/{endpoint}/requests/{request_id}",
        }
        return request_id

    async def fetch(self, task_id: str) -> VideoResult:
        routes = self._routes.get(task_id)
        if routes is None:
            raise SeedanceError(
                f"unknown fal request {task_id} — fetch() must use the same client as submit()"
            )

        status_body = await self._request("GET", routes["status"])
        raw_status = str(status_body.get("status", "")).upper()
        status = _FAL_STATUS.get(raw_status)

        if status is None:
            # fal signals failure with an ERROR-ish status or an explicit error payload.
            if raw_status:
                return VideoResult(
                    task_id=task_id,
                    status="failed",
                    error=f"fal status {raw_status}",
                    raw=status_body,
                )
            return VideoResult(task_id=task_id, status="unknown", raw=status_body)

        if status != TERMINAL_OK:
            return VideoResult(task_id=task_id, status=status, raw=status_body)

        result = await self._request("GET", routes["response"])
        video = result.get("video") or {}
        url = video.get("url") if isinstance(video, dict) else None
        if not url:
            return VideoResult(
                task_id=task_id,
                status="failed",
                error=f"completed but no video url: {result}",
                raw=result,
            )
        return VideoResult(
            task_id=task_id,
            status=TERMINAL_OK,
            video_url=url,
            raw=result,
            # Deliberately empty: fal reports no token usage, and this tool never
            # invents a cost. Spend has to be read off the fal dashboard.
            usage={},
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
            # Same reasoning as the BytePlus client: a finished job still holds a slot.
            interval = min(interval * 1.25, 8.0)
        raise SeedanceError(f"fal request {task_id} did not finish within {max_wait}s")

    async def generate(self, job: VideoJob, **wait_kw: Any) -> VideoResult:
        return await self.wait(await self.submit(job), **wait_kw)


def client_for(provider: str, api_key: str, *, base_url: str | None = None, **kw: Any):
    """Build the right client for a credential's provider."""
    from .client import ARK_INTERNATIONAL, SeedanceClient

    if provider == "byteplus":
        return SeedanceClient(api_key, base_url=base_url or ARK_INTERNATIONAL, **kw)
    if provider == "fal":
        return FalClient(api_key, base_url=base_url or FAL_QUEUE, **kw)
    raise SeedanceError(
        f"unknown provider {provider!r}; supported: byteplus, fal"
    )
