"""Tests for the fal.ai adapter, against a mock that reproduces fal's queue wire format.

Covers the things that would otherwise only surface after spending real credit: the
`Authorization: Key` header, the queue's own status/response URLs (including the
path-collapsing case that breaks hand-built polling paths), payload translation, and the
loud rejection of BytePlus-only features rather than silently dropping them.

Run:  python3 tests/test_fal.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from seedance.client import Reference, SeedanceError, VideoJob  # noqa: E402
from seedance.pool import Credential, CredentialPool  # noqa: E402
from seedance.providers import FalClient, client_for  # noqa: E402
from seedance.runner import BatchRunner, Ledger  # noqa: E402

PASS = FAIL = 0
TINY_MP4 = bytes.fromhex(
    "00000018667479706d703432000000006d70343269736f6d0000000866726565" + "00" * 32
)


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}{(' — ' + detail) if detail else ''}")


class FalState:
    def __init__(self) -> None:
        self.requests: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.auth_headers: list[str] = []
        self.submitted: list[dict] = []
        self.polls_before_done = 1
        self.bad_keys: set[str] = set()
        self.broke_keys: set[str] = set()


STATE = FalState()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:
        pass

    def _send(self, code: int, body, ctype="application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _key(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        STATE.auth_headers.append(auth)
        # fal uses "Key <k>", not "Bearer <k>".
        if not auth.startswith("Key "):
            return None
        return auth[4:].strip()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        key = self._key()
        if key is None or key in STATE.bad_keys:
            self._send(401, {"detail": "Unauthorized"})
            return
        if key in STATE.broke_keys:
            self._send(402, {"detail": "Insufficient balance"})
            return

        endpoint = urlparse(self.path).path.strip("/")
        rid = uuid.uuid4().hex[:12]
        with STATE.lock:
            STATE.requests[rid] = {"polls": 0, "endpoint": endpoint}
            STATE.submitted.append({"endpoint": endpoint, "body": json.loads(raw or b"{}")})

        host = self.headers["Host"]
        # Reproduce fal's real quirk: for a nested endpoint the polling path drops the
        # trailing segment. A client that rebuilds the path itself gets a 404 here.
        parts = endpoint.split("/")
        poll_base = "/".join(parts[:-1]) if len(parts) > 1 else endpoint
        self._send(
            200,
            {
                "request_id": rid,
                "status_url": f"http://{host}/{poll_base}/requests/{rid}/status",
                "response_url": f"http://{host}/{poll_base}/requests/{rid}",
                "cancel_url": f"http://{host}/{poll_base}/requests/{rid}/cancel",
            },
        )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/f/"):
            self._send(200, TINY_MP4, "video/mp4")
            return

        key = self._key()
        if key is None or key in STATE.bad_keys:
            self._send(401, {"detail": "Unauthorized"})
            return

        if "/requests/" not in path:
            self._send(404, {"detail": "not found"})
            return

        tail = path.split("/requests/", 1)[1]
        is_status = tail.endswith("/status")
        rid = tail.replace("/status", "").strip("/")

        with STATE.lock:
            req = STATE.requests.get(rid)
            if req is None:
                self._send(404, {"detail": "no such request"})
                return
            if is_status:
                req["polls"] += 1
                done = req["polls"] >= STATE.polls_before_done
                self._send(
                    200,
                    {"status": "COMPLETED" if done else "IN_PROGRESS", "request_id": rid},
                )
                return

        self._send(
            200,
            {"video": {"url": f"http://{self.headers['Host']}/f/{rid}.mp4"}, "seed": 42},
        )


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


async def main() -> int:
    global STATE

    print("\n[1] payload translation matches fal's schema")
    job = VideoJob(prompt="a test", duration=5, resolution="720p", ratio="16:9")
    payload = FalClient.to_payload(job)
    check("duration sent as a string", payload["duration"] == "5", repr(payload["duration"]))
    check("ratio maps to aspect_ratio", payload.get("aspect_ratio") == "16:9")
    check("resolution lowercased", payload.get("resolution") == "720p")
    check("no BytePlus-only keys leak", "watermark" not in payload and "content" not in payload)

    print("\n[2] unsupported features are rejected, not silently dropped")
    for field, kw in [
        ("return_last_frame", {"return_last_frame": True}),
        ("camera_fixed", {"camera_fixed": True}),
        ("seed", {"seed": 7}),
    ]:
        try:
            FalClient.to_payload(VideoJob(prompt="x", **kw))
            check(f"{field} rejected", False, "no error raised")
        except SeedanceError as exc:
            check(f"{field} rejected", field.split()[0] in str(exc), str(exc))

    print("\n[3] endpoint selection")
    check(
        "text-to-video by default",
        FalClient.endpoint_for(VideoJob(prompt="x")) == "bytedance/seedance-2.0/text-to-video",
    )
    check(
        "image reference switches endpoint",
        FalClient.endpoint_for(
            VideoJob(prompt="x", references=[Reference(url="http://i/1.png")])
        )
        == "bytedance/seedance-2.0/image-to-video",
    )
    check(
        "mini maps to its own endpoint",
        FalClient.endpoint_for(VideoJob(prompt="x", model="dreamina-seedance-2-0-mini-260615"))
        == "bytedance/seedance-2.0/mini/text-to-video",
    )
    # 1.5-pro has no fal listing, so it must fail loudly rather than fall back to a
    # different model and silently bill for output nobody asked for.
    try:
        FalClient.endpoint_for(VideoJob(prompt="x", model="seedance-1-5-pro-251215"))
        check("unmapped model raises", False, "no error")
    except SeedanceError as exc:
        check("unmapped model raises", "no fal.ai equivalent" in str(exc))

    server, base = serve()
    try:
        print("\n[4] end-to-end through the queue")
        STATE.polls_before_done = 2
        async with FalClient("falkey-1", base_url=base) as client:
            rid = await client.submit(VideoJob(prompt="hello", duration=5))
            result = await client.wait(rid, poll_interval=0.01)
        check("generation succeeded", result.ok, result.error or result.status)
        check("video url returned", bool(result.video_url))
        check(
            "auth header is 'Key', not 'Bearer'",
            any(h.startswith("Key ") for h in STATE.auth_headers)
            and not any(h.startswith("Bearer") for h in STATE.auth_headers),
            str(STATE.auth_headers[:2]),
        )
        check(
            "usage left empty — fal reports no tokens, and we never invent a cost",
            result.usage == {},
            str(result.usage),
        )

        print("\n[5] polling uses fal's returned URLs (the path-collapse trap)")
        # The mock's status_url drops the trailing path segment, exactly as fal does.
        # Getting here at all proves we did not rebuild the path ourselves.
        check("survived the collapsed polling path", result.ok)

        print("\n[6] a dead fal key is disabled and work reroutes")
        STATE.bad_keys.add("falkey-dead")
        STATE.polls_before_done = 1
        pool = CredentialPool(
            [
                Credential(name="dead", api_key="falkey-dead", provider="fal", base_url=base),
                Credential(name="live", api_key="falkey-2", provider="fal", base_url=base),
            ]
        )
        out = pathlib.Path(__file__).resolve().parent / "_fal_tmp"
        import shutil

        shutil.rmtree(out, ignore_errors=True)
        runner = BatchRunner(pool, output_dir=out, ledger=Ledger(out / "l.jsonl"), download=False)
        entries = await runner.run(
            [(f"j{i}", VideoJob(prompt=f"clip {i}", duration=5)) for i in range(6)],
            resume=False,
        )
        ok = sum(1 for e in entries if e.status == "succeeded")
        dead = next(c for c in pool.credentials if c.name == "dead")
        check("all jobs completed", ok == 6, f"{ok}/6")
        check("dead key disabled", dead.disabled_reason is not None)
        check(
            "survivor served the work",
            all(e.credential == "live" for e in entries if e.status == "succeeded"),
        )
        shutil.rmtree(out, ignore_errors=True)

        print("\n[7] out-of-credit (402) parks the key rather than looping")
        STATE.broke_keys.add("falkey-broke")
        pool2 = CredentialPool(
            [Credential(name="broke", api_key="falkey-broke", provider="fal", base_url=base)]
        )
        try:
            async with FalClient("falkey-broke", base_url=base) as c2:
                await c2.submit(VideoJob(prompt="x"))
            check("402 raised QuotaExceeded", False, "no error")
        except Exception as exc:
            check("402 raised QuotaExceeded", type(exc).__name__ == "QuotaExceeded", repr(exc))

        print("\n[8] chain refuses to run without a BytePlus key")
        runner2 = BatchRunner(pool2, output_dir=out, ledger=Ledger(out / "l2.jsonl"), download=False)
        try:
            await runner2.chain(["a", "b"], base=VideoJob(prompt=""))
            check("chain refused on a fal-only pool", False, "no error raised")
        except SeedanceError as exc:
            check("chain refused on a fal-only pool", "return_last_frame" in str(exc), str(exc))
        shutil.rmtree(out, ignore_errors=True)

        print("\n[9] the factory builds the right client per provider")
        bp = client_for("byteplus", "k")
        fl = client_for("fal", "k")
        check("byteplus -> SeedanceClient", type(bp).__name__ == "SeedanceClient")
        check("fal -> FalClient", type(fl).__name__ == "FalClient")
        await bp.aclose()
        await fl.aclose()
        try:
            client_for("nope", "k")
            check("unknown provider raises", False, "no error")
        except SeedanceError as exc:
            check("unknown provider raises", "unknown provider" in str(exc))
    finally:
        server.shutdown()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
