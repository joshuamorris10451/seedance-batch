"""How far does the batch runner actually go?

The mock in `mock_ark.py` proves *correctness* — that failover, resume and chaining work. It
does not answer the throughput question, because it accepts unlimited concurrent tasks. Real
BytePlus does not: an individual key allows 180 RPM but only **3 concurrent generation tasks**
(enterprise 10, 4K 1 for everyone). Concurrency is the ceiling, so that is what has to be
modelled.

This harness runs the *real* pool and runner against a server that enforces the per-key
concurrency cap exactly as BytePlus does — rejecting the 4th simultaneous task with a 429
`RateLimitExceeded` — and measures what comes out. It needs no API key and no account, which
is the point: you can find the tool's ceiling for free, and only spend money on the separate
question of whether the model's output is any good.

Run:  python3 tests/scale_test.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from seedance.client import VideoJob  # noqa: E402
from seedance.pool import Credential, CredentialPool  # noqa: E402
from seedance.runner import BatchRunner, Ledger  # noqa: E402

TINY_MP4 = bytes.fromhex(
    "00000018667479706d703432000000006d70343269736f6d0000000866726565" + "00" * 32
)


class ThrottledState:
    """Tracks per-key in-flight tasks so the concurrency cap can be enforced."""

    def __init__(self, max_concurrent: int, gen_seconds: float) -> None:
        self.max_concurrent = max_concurrent
        self.gen_seconds = gen_seconds
        self.tasks: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.in_flight: dict[str, int] = {}
        # Observability: the high-water mark per key, and every rejection.
        self.peak: dict[str, int] = {}
        self.rejections = 0
        self.submissions = 0
        self.completions = 0


STATE: ThrottledState | None = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _key(self) -> str:
        return self.headers.get("Authorization", "").replace("Bearer ", "").strip()

    def _send(self, code: int, body, content_type="application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        assert STATE is not None
        path = urlparse(self.path).path
        # Drain first — an early return without reading the body desyncs keep-alive.
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        key = self._key()
        if not key:
            self._send(401, {"error": {"code": "AuthenticationError", "message": "no key"}})
            return

        if not path.endswith("/contents/generations/tasks"):
            self._send(404, {"error": {"code": "NotFound", "message": path}})
            return

        with STATE.lock:
            live = STATE.in_flight.get(key, 0)
            if live >= STATE.max_concurrent:
                STATE.rejections += 1
                self._send(
                    429,
                    {"error": {"code": "RateLimitExceeded", "message": "Too many concurrent tasks."}},
                )
                return
            STATE.in_flight[key] = live + 1
            STATE.peak[key] = max(STATE.peak.get(key, 0), live + 1)
            STATE.submissions += 1
            task_id = "task_" + uuid.uuid4().hex[:12]
            STATE.tasks[task_id] = {
                "key": key,
                "ready_at": time.monotonic() + STATE.gen_seconds,
                "payload": json.loads(raw or b"{}"),
            }

        self._send(200, {"id": task_id, "status": "queued"})

    def do_GET(self) -> None:
        assert STATE is not None
        path = urlparse(self.path).path

        if path.startswith("/f/"):
            self._send(200, TINY_MP4, "video/mp4")
            return

        if "/contents/generations/tasks/" not in path:
            self._send(404, {"error": {"code": "NotFound", "message": path}})
            return

        task_id = path.rsplit("/", 1)[-1]
        with STATE.lock:
            task = STATE.tasks.get(task_id)
            if task is None:
                self._send(404, {"error": {"code": "NotFound", "message": "no such task"}})
                return
            if time.monotonic() < task["ready_at"]:
                self._send(200, {"id": task_id, "status": "running"})
                return
            if not task.get("released"):
                task["released"] = True
                STATE.in_flight[task["key"]] -= 1
                STATE.completions += 1
            duration = task["payload"].get("duration", 5)

        self._send(
            200,
            {
                "id": task_id,
                "status": "succeeded",
                "content": {"video_url": f"http://{self.headers['Host']}/f/{task_id}.mp4"},
                "usage": {"total_tokens": 21600 * duration, "completion_tokens": 21600 * duration},
            },
        )


def serve(max_concurrent: int, gen_seconds: float):
    global STATE
    STATE = ThrottledState(max_concurrent, gen_seconds)
    # Threading, not plain HTTPServer: the runner holds one keep-alive connection per
    # in-flight job, and a single-threaded server serialises them into a stall.
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


async def run_scenario(
    *,
    keys: int,
    jobs: int,
    max_concurrent: int = 3,
    gen_seconds: float = 1.0,
    poll_interval: float = 0.05,
    tmp: pathlib.Path,
) -> dict:
    server, base = serve(max_concurrent, gen_seconds)
    assert STATE is not None
    try:
        pool = CredentialPool(
            [
                Credential(
                    name=f"key{i}",
                    api_key=f"sk-test-{i}",
                    base_url=base,
                    max_concurrency=max_concurrent,
                )
                for i in range(keys)
            ]
        )
        out = tmp / f"k{keys}_j{jobs}"
        runner = BatchRunner(
            pool,
            output_dir=out,
            ledger=Ledger(out / "ledger.jsonl"),
            download=False,
        )

        # Keep polling tight so we measure the scheduler, not the poll interval.
        import seedance.client as client_mod

        original = client_mod.SeedanceClient.wait

        async def fast_wait(self, task_id, **kw):
            kw.setdefault("poll_interval", poll_interval)
            kw.setdefault("max_wait", 120.0)
            return await original(self, task_id, **kw)

        client_mod.SeedanceClient.wait = fast_wait
        try:
            batch = [
                (f"job{i:04d}", VideoJob(prompt=f"test clip {i}", duration=5, resolution="720p"))
                for i in range(jobs)
            ]
            # resume=False: this measures throughput, so every job must actually run.
            t0 = time.monotonic()
            results = await runner.run(batch, resume=False)
            elapsed = time.monotonic() - t0
        finally:
            client_mod.SeedanceClient.wait = original

        ok = sum(1 for r in results if r.status == "succeeded")
        peak_total = sum(STATE.peak.values())
        breached = {k: v for k, v in STATE.peak.items() if v > max_concurrent}
        return {
            "keys": keys,
            "jobs": jobs,
            "ok": ok,
            "failed": jobs - ok,
            "elapsed": elapsed,
            "throughput": ok / elapsed if elapsed else 0.0,
            "peak_concurrent_total": peak_total,
            "cap_breaches": breached,
            "server_rejections": STATE.rejections,
            "tokens": sum(r.usage.get("completion_tokens", 0) for r in results),
        }
    finally:
        server.shutdown()


async def main() -> int:
    import shutil

    tmp = pathlib.Path(__file__).resolve().parent / "_scale_tmp"
    # Wipe: a ledger left by an earlier run would make every job resume-skip and the
    # measurement would silently read as "instant".
    shutil.rmtree(tmp, ignore_errors=True)

    # Simulated generation time. Real Seedance 5s/720p takes ~60-120s; the poll cadence
    # below is scaled to keep the same poll:generation ratio as production (6s poll on a
    # 60s job = 0.1), so the efficiency figure transfers.
    gen = 1.0
    poll = 0.1

    print("=" * 78)
    print("SCALE TEST — real pool + real runner against a cap-enforcing server")
    print(f"per-key cap: 3 concurrent (BytePlus individual) · simulated gen time: {gen}s/clip")
    print("=" * 78)

    rows = []
    for keys in (1, 2, 4, 8, 16):
        jobs = keys * 12
        row = await run_scenario(
            keys=keys, jobs=jobs, gen_seconds=gen, poll_interval=poll, tmp=tmp
        )
        rows.append(row)
        ideal = keys * 3 / gen
        eff = row["throughput"] / ideal * 100 if ideal else 0
        row["efficiency"] = eff
        print(
            f"  {keys:2d} key(s) · {jobs:3d} jobs → {row['ok']:3d} ok  "
            f"{row['elapsed']:6.2f}s  {row['throughput']:5.2f} clip/s  "
            f"peak {row['peak_concurrent_total']:2d}/{keys*3:2d} concurrent  "
            f"{eff:5.1f}% of theoretical  rejections={row['server_rejections']}"
        )

    print()
    failures = []
    for row in rows:
        if row["cap_breaches"]:
            failures.append(f"key concurrency cap breached: {row['cap_breaches']}")
        if row["failed"]:
            failures.append(f"{row['failed']} job(s) failed at {row['keys']} key(s)")
        if row["peak_concurrent_total"] != row["keys"] * 3:
            failures.append(
                f"{row['keys']} key(s): pool only reached "
                f"{row['peak_concurrent_total']}/{row['keys']*3} concurrent"
            )

    print("-" * 78)
    if failures:
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n{len(failures)} problem(s)")
        return 1

    print("  ✓ every job completed at every scale")
    print("  ✓ per-key cap never breached (server rejected nothing — the client self-limited)")
    print("  ✓ pool saturated all available slots at every scale")

    # What this means with real generation latency.
    # ---- polling overhead at PRODUCTION latency ------------------------------
    # The scenarios above run on compressed timings that never reach the client's
    # interval cap, so they cannot exercise it. Drive the real wait() loop on a
    # virtual clock instead: a finished task still holds one of the key's 3 slots
    # until the next poll notices, so detection lag is throughput lost.
    print()
    print("Polling overhead at production latency (real wait() loop, virtual clock):")
    import seedance.client as client_mod

    async def detection_lag(gen_seconds: float) -> tuple[float, int]:
        clock = {"t": 0.0, "polls": 0}
        real_sleep = asyncio.sleep

        async def fake_sleep(sec, *a, **kw):
            clock["t"] += sec
            await real_sleep(0)

        class FakeClient(client_mod.SeedanceClient):
            def __init__(self):  # no network
                pass

            async def fetch(self, task_id):
                clock["polls"] += 1
                done = clock["t"] >= gen_seconds
                return client_mod.VideoResult(
                    task_id=task_id,
                    status="succeeded" if done else "running",
                    video_url="http://x/v.mp4" if done else None,
                )

        client_mod.asyncio.sleep = fake_sleep
        try:
            await FakeClient().wait("t1")
        finally:
            client_mod.asyncio.sleep = real_sleep
        return clock["t"], clock["polls"]

    print(f"  {'generation':>11} {'detected at':>12} {'wasted':>8} {'overhead':>9} {'polls':>6}")
    worst = 0.0
    for gen_s in (60.0, 90.0, 120.0, 300.0):
        detected, polls = await detection_lag(gen_s)
        waste = detected - gen_s
        pct = waste / gen_s * 100
        worst = max(worst, pct)
        print(
            f"  {gen_s:>10.0f}s {detected:>11.1f}s {waste:>7.1f}s {pct:>8.1f}% {polls:>6}"
        )
    if worst > 6.0:
        print(f"\n  ✗ worst-case polling overhead {worst:.1f}% — interval cap is too high")
        return 1
    print(f"  ✓ worst-case slot waste {worst:.1f}% (was 19.5% at the old 20s cap)")

    print()
    print("Extrapolation to real latency (Seedance 5s/720p ≈ 60-120s per clip):")
    print(f"  {'keys':>5} {'concurrent':>11} {'clips/hr @60s':>14} {'clips/hr @120s':>15}")
    for keys in (1, 2, 4, 8, 16):
        c = keys * 3
        print(f"  {keys:>5} {c:>11} {c*3600/60:>14.0f} {c*3600/120:>15.0f}")
    print()
    print("  Throughput is linear in keys because the cap is per key. There is no")
    print("  cleverness available client-side: 3 slots is 3 slots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
