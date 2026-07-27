"""A stand-in for BytePlus ModelArk, matching the documented wire format.

Lets the full pipeline be exercised without a paid key: task create → polling → terminal
state → download. Also simulates the failure modes the runner has to survive (429 throttling,
quota exhaustion, bad auth) so the pool's failover is genuinely tested rather than assumed.
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# Minimal valid MP4/PNG payloads so downloads produce real files.
TINY_MP4 = bytes.fromhex(
    "00000018667479706d703432000000006d70343269736f6d"
    "0000000866726565" + "00" * 32
)
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "05570cf5a10000000049454e44ae426082"
)


class State:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.calls = 0
        self.lock = threading.Lock()
        # Keys that should behave badly, so failover can be tested.
        self.bad_auth: set[str] = set()
        self.out_of_quota: set[str] = set()
        self.throttle_once: set[str] = set()
        self._throttled: set[str] = set()
        # Number of polls before a task reports success.
        self.polls_until_done = 1


STATE = State()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silence
        pass

    # -- helpers ---------------------------------------------------------
    def _key(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth.replace("Bearer ", "").strip()

    def _send(self, code: int, body: dict | bytes, content_type="application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code: int, err_code: str, message: str) -> None:
        self._send(code, {"error": {"code": err_code, "message": message, "type": "error"}})

    def _guard(self) -> bool:
        key = self._key()
        if not key or key in STATE.bad_auth:
            self._error(401, "AuthenticationError", "The API key format is incorrect.")
            return False
        if key in STATE.out_of_quota:
            self._error(429, "QuotaExceeded", "Your account balance is insufficient.")
            return False
        if key in STATE.throttle_once and key not in STATE._throttled:
            STATE._throttled.add(key)
            self._error(429, "RateLimitExceeded", "Too many concurrent tasks.")
            return False
        return True

    # -- routes ----------------------------------------------------------
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        # Drain the body BEFORE any early return, or keep-alive desyncs and the next
        # request on this connection is parsed as garbage.
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b"{}"
        if not self._guard():
            return
        payload = json.loads(raw_body or b"{}")

        with STATE.lock:
            STATE.calls += 1

        if path.endswith("/contents/generations/tasks"):
            task_id = "task_" + uuid.uuid4().hex[:12]
            with STATE.lock:
                STATE.tasks[task_id] = {"polls": 0, "payload": payload}
            self._send(200, {"id": task_id, "model": payload.get("model"), "status": "queued"})
            return

        if path.endswith("/images/generations"):
            n = (payload.get("sequential_image_generation_options") or {}).get("max_images", 1)
            self._send(
                200,
                {
                    "model": payload.get("model"),
                    "data": [{"url": f"http://{self.headers['Host']}/f/img{i}.png"} for i in range(n)],
                    "usage": {"total_tokens": 1200 * n, "generated_images": n},
                },
            )
            return

        self._error(404, "NotFound", path)

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path.startswith("/f/"):
            if path.endswith(".png"):
                self._send(200, TINY_PNG, "image/png")
            else:
                self._send(200, TINY_MP4, "video/mp4")
            return

        if not self._guard():
            return

        if "/contents/generations/tasks/" in path:
            task_id = path.rsplit("/", 1)[-1]
            with STATE.lock:
                task = STATE.tasks.get(task_id)
                if task is None:
                    self._error(404, "NotFound", "no such task")
                    return
                task["polls"] += 1
                polls = task["polls"]
                payload = task["payload"]

            if polls < STATE.polls_until_done:
                self._send(200, {"id": task_id, "status": "running"})
                return

            host = self.headers["Host"]
            content = {"video_url": f"http://{host}/f/{task_id}.mp4"}
            if payload.get("return_last_frame"):
                content["last_frame_url"] = f"http://{host}/f/{task_id}_last.png"
            duration = payload.get("duration", 5)
            self._send(
                200,
                {
                    "id": task_id,
                    "status": "succeeded",
                    "content": content,
                    "usage": {"total_tokens": 48600 * duration, "completion_tokens": 48600 * duration},
                },
            )
            return

        if path.endswith("/contents/generations/tasks"):
            self._send(200, {"items": [], "total": 0})
            return

        self._error(404, "NotFound", path)

    def do_DELETE(self) -> None:
        if not self._guard():
            return
        self._send(200, {})


def serve() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"
