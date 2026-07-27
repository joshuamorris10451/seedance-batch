"""Seedream image generation on the same BytePlus ModelArk key as Seedance video.

Images are synchronous (`POST /images/generations`), unlike video which is task-based.
Seedream 5.0 natively supports batch generation and multi-reference image-to-image, so a
single call can return several images from one prompt plus reference images.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Sequence

import httpx

from .client import AuthFailed, QuotaExceeded, SeedanceError

# Verified present in the live BytePlus ModelArk model list on 2026-07-27.
IMAGE_MODELS = {
    "5.0-pro": "seedream-5-0-pro-260628",
    "5.0": "seedream-5-0-260128",
    "5.0-lite": "seedream-5-0-lite-260128",
    "4.5": "seedream-4-5-251128",
    "4.0": "seedream-4-0-250828",
}


@dataclasses.dataclass(slots=True)
class ImageJob:
    prompt: str
    model: str = IMAGE_MODELS["5.0"]
    size: str = "2K"
    # Seedream returns a batch in one call; this is images per prompt.
    n: int = 1
    # Reference images for image-to-image / multi-reference composition.
    reference_urls: Sequence[str] = ()
    seed: int | None = None
    watermark: bool = False
    response_format: str = "url"
    sequential: str | None = None
    tag: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": self.prompt,
            "size": self.size,
            "response_format": self.response_format,
            "watermark": self.watermark,
        }
        if self.n > 1:
            # Seedream's batch mode is driven by sequential_image_generation.
            payload["sequential_image_generation"] = self.sequential or "auto"
            payload["sequential_image_generation_options"] = {"max_images": self.n}
        if self.reference_urls:
            urls = list(self.reference_urls)
            payload["image"] = urls[0] if len(urls) == 1 else urls
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload


@dataclasses.dataclass(slots=True)
class ImageResult:
    urls: list[str]
    error: str | None = None
    usage: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.urls) and not self.error


class SeedreamClient:
    def __init__(self, api_key: str, *, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "SeedreamClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(self, job: ImageJob) -> ImageResult:
        resp = await self._client.post(
            f"{self.base_url}/images/generations", json=job.to_payload()
        )
        try:
            body = resp.json()
        except Exception:
            body = {}

        if resp.status_code >= 300:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code") or str(resp.status_code)
            msg = err.get("message") or resp.text[:300]
            if resp.status_code in (401, 403):
                raise AuthFailed(msg, code=code, status=resp.status_code)
            if resp.status_code == 429 or code in {"QuotaExceeded", "AccountOverdueError"}:
                raise QuotaExceeded(msg, code=code, status=resp.status_code)
            raise SeedanceError(msg, code=code, status=resp.status_code)

        urls: list[str] = []
        for item in body.get("data", []) or []:
            if item.get("url"):
                urls.append(item["url"])
            elif item.get("b64_json"):
                urls.append("data:image/png;base64," + item["b64_json"])

        return ImageResult(urls=urls, usage=body.get("usage") or {}, raw=body)
