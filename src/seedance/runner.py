"""Batch runner: a job list in, finished files on disk out.

Design notes:
- Every job is checkpointed to a JSONL ledger the moment its state changes. A crash, a
  killed terminal or a laptop closing mid-run costs you nothing: re-running skips anything
  already `succeeded`.
- Cost is recorded from the provider's own `usage` block, never estimated. If a provider
  doesn't report usage we record null rather than inventing a number.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import pathlib
import time
from typing import Any, Callable, Iterable, Sequence

import httpx

from .client import (
    ARK_INTERNATIONAL,
    SeedanceClient,
    SeedanceError,
    VideoJob,
    VideoResult,
)
from .images import ImageJob, ImageResult, SeedreamClient
from .pool import Credential, CredentialPool

log = logging.getLogger(__name__)


@dataclasses.dataclass
class LedgerEntry:
    job_id: str
    kind: str
    status: str
    tag: str | None = None
    task_id: str | None = None
    output: list[str] = dataclasses.field(default_factory=list)
    files: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None
    credential: str | None = None
    usage: dict[str, Any] = dataclasses.field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)


class Ledger:
    """Append-only JSONL run log that doubles as the resume index."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._state: dict[str, LedgerEntry] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                self._state[row["job_id"]] = LedgerEntry(**row)
            except Exception:
                continue

    def done(self, job_id: str) -> bool:
        entry = self._state.get(job_id)
        return bool(entry and entry.status == "succeeded")

    def get(self, job_id: str) -> LedgerEntry | None:
        return self._state.get(job_id)

    async def write(self, entry: LedgerEntry) -> None:
        async with self._lock:
            self._state[entry.job_id] = entry
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(entry.to_json() + "\n")

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self._state.values():
            out[entry.status] = out.get(entry.status, 0) + 1
        return out


async def _download(url: str, dest: pathlib.Path, *, timeout: float = 300.0) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    fh.write(chunk)
    tmp.replace(dest)
    return dest


class BatchRunner:
    def __init__(
        self,
        pool: CredentialPool,
        *,
        output_dir: pathlib.Path,
        ledger: Ledger | None = None,
        download: bool = True,
        progress: Callable[[LedgerEntry], None] | None = None,
    ):
        self.pool = pool
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or Ledger(self.output_dir / "ledger.jsonl")
        self.download = download
        self.progress = progress

    def _emit(self, entry: LedgerEntry) -> None:
        if self.progress:
            try:
                self.progress(entry)
            except Exception:
                log.exception("progress callback raised")

    async def _run_video(self, job_id: str, job: VideoJob) -> LedgerEntry:
        entry = LedgerEntry(
            job_id=job_id, kind="video", status="running", tag=job.tag, started_at=time.time()
        )
        self._emit(entry)

        async def call(cred: Credential) -> VideoResult:
            entry.credential = cred.name
            async with SeedanceClient(
                cred.api_key, base_url=cred.base_url or ARK_INTERNATIONAL
            ) as client:
                task_id = await client.submit(job)
                entry.task_id = task_id
                return await client.wait(task_id)

        try:
            result = await self.pool.run(call, provider="byteplus")
        except Exception as exc:
            entry.status = "failed"
            entry.error = str(exc)
            entry.finished_at = time.time()
            await self.ledger.write(entry)
            self._emit(entry)
            return entry

        entry.usage = result.usage
        if not result.ok:
            entry.status = "failed"
            entry.error = result.error or f"terminal status {result.status}"
        else:
            entry.status = "succeeded"
            entry.output = [result.video_url] if result.video_url else []
            if result.last_frame_url:
                entry.output.append(result.last_frame_url)
            if self.download and result.video_url:
                dest = self.output_dir / "video" / f"{job_id}.mp4"
                try:
                    await _download(result.video_url, dest)
                    entry.files = [str(dest)]
                except Exception as exc:
                    # The generation succeeded and cost money — never mark it failed just
                    # because the download did. Keep the URL so it can be fetched later.
                    entry.error = f"generated but download failed: {exc}"

        entry.finished_at = time.time()
        await self.ledger.write(entry)
        self._emit(entry)
        return entry

    async def _run_image(self, job_id: str, job: ImageJob) -> LedgerEntry:
        entry = LedgerEntry(
            job_id=job_id, kind="image", status="running", tag=job.tag, started_at=time.time()
        )
        self._emit(entry)

        async def call(cred: Credential) -> ImageResult:
            entry.credential = cred.name
            async with SeedreamClient(
                cred.api_key, base_url=cred.base_url or ARK_INTERNATIONAL
            ) as client:
                return await client.generate(job)

        try:
            result = await self.pool.run(call, provider="byteplus")
        except Exception as exc:
            entry.status = "failed"
            entry.error = str(exc)
            entry.finished_at = time.time()
            await self.ledger.write(entry)
            self._emit(entry)
            return entry

        entry.usage = result.usage
        if not result.ok:
            entry.status = "failed"
            entry.error = result.error or "no images returned"
        else:
            entry.status = "succeeded"
            entry.output = result.urls
            if self.download:
                files = []
                for idx, url in enumerate(result.urls):
                    if url.startswith("data:"):
                        continue
                    dest = self.output_dir / "image" / f"{job_id}_{idx}.png"
                    try:
                        await _download(url, dest)
                        files.append(str(dest))
                    except Exception as exc:
                        entry.error = f"generated but download failed: {exc}"
                entry.files = files

        entry.finished_at = time.time()
        await self.ledger.write(entry)
        self._emit(entry)
        return entry

    async def run(
        self,
        jobs: Sequence[tuple[str, VideoJob | ImageJob]],
        *,
        resume: bool = True,
    ) -> list[LedgerEntry]:
        """Run every job. Concurrency is governed by the pool, not by a fixed worker count."""
        pending = [
            (jid, job)
            for jid, job in jobs
            if not (resume and self.ledger.done(jid))
        ]
        skipped = len(jobs) - len(pending)
        if skipped:
            log.info("resuming: skipping %d already-completed job(s)", skipped)

        async def dispatch(jid: str, job: VideoJob | ImageJob) -> LedgerEntry:
            if isinstance(job, VideoJob):
                return await self._run_video(jid, job)
            return await self._run_image(jid, job)

        tasks = [asyncio.create_task(dispatch(jid, job)) for jid, job in pending]
        results: list[LedgerEntry] = []
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
        return results

    async def chain(
        self,
        prompts: Sequence[str],
        *,
        base: VideoJob,
        job_id_prefix: str = "chain",
    ) -> list[LedgerEntry]:
        """Generate a continuous sequence by feeding each clip's last frame into the next.

        This uses Seedance's `return_last_frame` plus image-to-video first-frame input, which
        is how you get past the 15-second per-generation ceiling with visual continuity.
        Inherently sequential, so it does not benefit from the pool's concurrency.
        """
        from .client import Reference

        entries: list[LedgerEntry] = []
        carry: str | None = None

        for idx, prompt in enumerate(prompts):
            job = dataclasses.replace(
                base,
                prompt=prompt,
                return_last_frame=True,
                references=(
                    [Reference(url=carry, kind="image", role="first_frame")] if carry else []
                ),
            )
            entry = await self._run_video(f"{job_id_prefix}_{idx:03d}", job)
            entries.append(entry)
            if entry.status != "succeeded":
                log.error("chain broke at clip %d: %s", idx, entry.error)
                break
            carry = entry.output[1] if len(entry.output) > 1 else None
            if not carry:
                log.error("clip %d returned no last frame; cannot continue the chain", idx)
                break

        return entries
