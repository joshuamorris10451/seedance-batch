"""End-to-end pipeline tests against the mock Ark server."""

from __future__ import annotations

import asyncio
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import mock_ark  # noqa: E402
from seedance.client import Reference, SeedanceClient, VideoJob  # noqa: E402
from seedance.images import ImageJob  # noqa: E402
from seedance.pool import Credential, CredentialPool  # noqa: E402
from seedance.runner import BatchRunner, Ledger  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if detail and not cond else ""))


async def main() -> int:
    server, base = mock_ark.serve()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="seedance-test-"))
    try:
        print("\n[1] single video generation + download")
        async with SeedanceClient("key-good", base_url=base) as client:
            job = VideoJob(prompt="test", duration=5, resolution="720p")
            result = await client.generate(job)
        check("task succeeds", result.ok)
        check("usage reported", result.usage.get("total_tokens") == 48600 * 5)

        print("\n[2] batch run, mixed video + image, concurrency spread")
        pool = CredentialPool([
            Credential(name="k1", api_key="key-1", base_url=base, max_concurrency=3),
            Credential(name="k2", api_key="key-2", base_url=base, max_concurrency=3),
        ])
        runner = BatchRunner(pool, output_dir=tmp / "run1")
        jobs = [(f"v{i}", VideoJob(prompt=f"clip {i}", duration=5)) for i in range(8)]
        jobs += [(f"i{i}", ImageJob(prompt=f"still {i}", n=2)) for i in range(4)]
        entries = await runner.run(jobs)
        ok = [e for e in entries if e.status == "succeeded"]
        check("all 12 jobs succeeded", len(ok) == 12, f"got {len(ok)}")
        videos = list((tmp / "run1" / "video").glob("*.mp4"))
        images = list((tmp / "run1" / "image").glob("*.png"))
        check("8 video files on disk", len(videos) == 8, f"got {len(videos)}")
        check("8 image files on disk (4 jobs x 2)", len(images) == 8, f"got {len(images)}")
        used = {e.credential for e in ok}
        check("work spread across both keys", used == {"k1", "k2"}, f"used {used}")

        print("\n[3] resume skips completed work")
        before = mock_ark.STATE.calls
        runner2 = BatchRunner(pool, output_dir=tmp / "run1")
        again = await runner2.run(jobs)
        check("nothing re-run on resume", len(again) == 0 and mock_ark.STATE.calls == before,
              f"{len(again)} re-run, calls {before}->{mock_ark.STATE.calls}")

        print("\n[4] failover: dead key is disabled, work completes on the survivor")
        mock_ark.STATE.bad_auth.add("key-dead")
        pool2 = CredentialPool([
            Credential(name="dead", api_key="key-dead", base_url=base, max_concurrency=3),
            Credential(name="live", api_key="key-live", base_url=base, max_concurrency=3),
        ])
        runner3 = BatchRunner(pool2, output_dir=tmp / "run2")
        entries3 = await runner3.run([(f"f{i}", VideoJob(prompt=f"c{i}")) for i in range(6)])
        ok3 = [e for e in entries3 if e.status == "succeeded"]
        dead = next(c for c in pool2.credentials if c.name == "dead")
        check("all jobs still completed", len(ok3) == 6, f"got {len(ok3)}")
        check("dead key auto-disabled", dead.disabled_reason is not None)
        check("survivor did the work", {e.credential for e in ok3} == {"live"})

        print("\n[5] quota exhaustion parks the key rather than looping")
        mock_ark.STATE.out_of_quota.add("key-broke")
        pool3 = CredentialPool([
            Credential(name="broke", api_key="key-broke", base_url=base, max_concurrency=2),
            Credential(name="rich", api_key="key-rich", base_url=base, max_concurrency=2),
        ])
        runner4 = BatchRunner(pool3, output_dir=tmp / "run3")
        entries4 = await runner4.run([(f"q{i}", VideoJob(prompt=f"c{i}")) for i in range(4)])
        broke = next(c for c in pool3.credentials if c.name == "broke")
        check("quota'd key disabled", broke.disabled_reason is not None)
        check("all work rerouted", sum(1 for e in entries4 if e.status == "succeeded") == 4)

        print("\n[6] transient 429 is retried, not failed")
        mock_ark.STATE.throttle_once.add("key-throttle")
        async with SeedanceClient("key-throttle", base_url=base, max_retries=3) as client:
            r = await client.generate(VideoJob(prompt="retry me"))
        check("recovered from 429", r.ok)

        print("\n[7] slow task: polling until terminal state")
        mock_ark.STATE.polls_until_done = 3
        async with SeedanceClient("key-slow", base_url=base) as client:
            tid = await client.submit(VideoJob(prompt="slow"))
            r = await client.wait(tid, poll_interval=0.05)
        check("polled to completion", r.ok)
        mock_ark.STATE.polls_until_done = 1

        print("\n[8] clip chaining feeds last frame into the next clip")
        pool4 = CredentialPool([Credential(name="c", api_key="key-c", base_url=base, max_concurrency=3)])
        runner5 = BatchRunner(pool4, output_dir=tmp / "run4")
        chained = await runner5.chain(
            ["scene one", "scene two", "scene three"],
            base=VideoJob(prompt="", duration=5),
        )
        check("3 clips chained", len(chained) == 3 and all(e.status == "succeeded" for e in chained))
        check("each clip returned a last frame", all(len(e.output) == 2 for e in chained))

        print("\n[9] ledger records usage for cost reporting")
        led = Ledger(tmp / "run1" / "ledger.jsonl")
        tokens = sum(int(e.usage.get("total_tokens") or 0) for e in led._state.values())
        check("usage accumulated in ledger", tokens > 0, f"tokens={tokens}")

        print("\n[10] download failure does not lose a paid generation")
        async with SeedanceClient("key-good", base_url=base) as client:
            res = await client.generate(VideoJob(prompt="x"))
        check("video_url retained", bool(res.video_url))

    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
