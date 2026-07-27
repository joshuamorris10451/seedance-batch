"""Configuration + credential loading.

Keys come from (in priority order): an explicit --config file, ./seedance.toml,
~/.config/seedance/config.toml, or environment variables. Keys are never written to the
ledger, never logged, and never printed — only credential *names* appear in output.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None  # type: ignore[assignment]

from .client import ARK_CHINA, ARK_INTERNATIONAL
from .pool import Credential
from .providers import FAL_QUEUE

DEFAULT_PATHS = (
    pathlib.Path("seedance.toml"),
    pathlib.Path.home() / ".config" / "seedance" / "config.toml",
)

PROVIDER_BASE_URLS = {
    "byteplus": ARK_INTERNATIONAL,
    "volcengine": ARK_CHINA,
    "fal": FAL_QUEUE,
}


def _redact(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}…{key[-4:]}"


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    quote = raw[:1]
    if quote in {'"', "'"}:
        # Take the quoted span only; anything after the closing quote is an inline comment.
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else raw[1:]
    # Unquoted: a '#' starts a comment.
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    low = raw.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_minimal_toml(text: str) -> dict[str, Any]:
    """Parse the small TOML subset this tool's config uses.

    Only needed on Python < 3.11 without `tomli` installed. Handles `[table]`,
    `[[array_of_tables]]`, and `key = value` with string/int/float/bool values plus
    `#` comments. Anything fancier should install tomli.
    """
    root: dict[str, Any] = {}
    current: dict[str, Any] = root

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[["):
            name = line[2:].split("]]", 1)[0].strip()
            root.setdefault(name, [])
            current = {}
            root[name].append(current)
            continue
        if line.startswith("["):
            name = line[1:].split("]", 1)[0].strip()
            current = root.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = _parse_scalar(value)

    return root


def load_config(path: pathlib.Path | None = None) -> dict[str, Any]:
    candidates = [path] if path else list(DEFAULT_PATHS)
    for candidate in candidates:
        if not candidate or not candidate.exists():
            continue
        if _toml is not None:
            with candidate.open("rb") as fh:
                data = _toml.load(fh)
        else:
            data = _parse_minimal_toml(candidate.read_text(encoding="utf-8"))
        data["_source"] = str(candidate)
        return data
    return {}


def load_credentials(config: dict[str, Any] | None = None) -> list[Credential]:
    """Build the credential list from config plus environment fallbacks."""
    config = config or {}
    creds: list[Credential] = []

    for idx, row in enumerate(config.get("credentials", []) or []):
        key = row.get("api_key") or os.environ.get(row.get("api_key_env", ""), "")
        if not key:
            continue
        provider = row.get("provider", "byteplus")
        creds.append(
            Credential(
                name=row.get("name") or f"{provider}-{idx + 1}",
                api_key=key,
                provider=provider,
                base_url=row.get("base_url") or PROVIDER_BASE_URLS.get(provider),
                max_concurrency=int(row.get("max_concurrency", 3)),
            )
        )

    if not creds:
        # Environment fallback: ARK_API_KEY, or ARK_API_KEY_1..N for several keys.
        single = os.environ.get("ARK_API_KEY")
        if single:
            creds.append(
                Credential(
                    name="env",
                    api_key=single,
                    provider="byteplus",
                    base_url=ARK_INTERNATIONAL,
                    max_concurrency=int(os.environ.get("ARK_CONCURRENCY", "3")),
                )
            )
        for n in range(1, 21):
            key = os.environ.get(f"ARK_API_KEY_{n}")
            if key:
                creds.append(
                    Credential(
                        name=f"env-{n}",
                        api_key=key,
                        provider="byteplus",
                        base_url=ARK_INTERNATIONAL,
                        max_concurrency=int(os.environ.get("ARK_CONCURRENCY", "3")),
                    )
                )

        # fal.ai: FAL_KEY is the variable their own SDK reads, so honour the same name.
        # This is the zero-friction path — a fal signup needs no card and no ID check.
        fal_key = os.environ.get("FAL_KEY")
        if fal_key:
            creds.append(
                Credential(
                    name="fal-env",
                    api_key=fal_key,
                    provider="fal",
                    base_url=FAL_QUEUE,
                    max_concurrency=int(os.environ.get("FAL_CONCURRENCY", "4")),
                )
            )
        for n in range(1, 21):
            key = os.environ.get(f"FAL_KEY_{n}")
            if key:
                creds.append(
                    Credential(
                        name=f"fal-env-{n}",
                        api_key=key,
                        provider="fal",
                        base_url=FAL_QUEUE,
                        max_concurrency=int(os.environ.get("FAL_CONCURRENCY", "4")),
                    )
                )

    return creds


def describe_credentials(creds: list[Credential]) -> str:
    if not creds:
        return "no credentials configured"
    lines = [
        f"  {c.name:<16} {c.provider:<12} conc={c.max_concurrency}  key={_redact(c.api_key)}"
        for c in creds
    ]
    total = sum(c.max_concurrency for c in creds)
    lines.append(f"  → {len(creds)} credential(s), {total} concurrent generation slots")
    return "\n".join(lines)


EXAMPLE_CONFIG = """\
# seedance.toml — your own API keys. Never commit this file.
#
# Get a BytePlus ModelArk key at:
#   https://console.byteplus.com/ark/region:ark+ap-southeast-1/apikey
#
# A BytePlus *individual* key allows 180 requests/min but only 3 CONCURRENT video tasks
# (enterprise keys get 10). Concurrency is the real throughput limit, so if you legitimately
# hold more than one key, list them all and the runner will spread work across them.

[[credentials]]
name = "byteplus-main"
provider = "byteplus"
api_key_env = "ARK_API_KEY"   # read from the environment; or use api_key = "..." directly
max_concurrency = 3

# [[credentials]]
# name = "byteplus-second"
# provider = "byteplus"
# api_key = "..."
# max_concurrency = 3

# fal.ai resells Seedance at exactly 2x the BytePlus token rate, so it is the wrong place
# to buy volume — but a new fal account gets $10 of credit against an email address, with
# no card and no identity check. That makes it the cheapest way to prove the pipeline works
# end to end before spending anything. Get a key at https://fal.ai/dashboard/keys
#
# [[credentials]]
# name = "fal-trial"
# provider = "fal"
# api_key_env = "FAL_KEY"
# max_concurrency = 4

[defaults]
model = "2.0"          # 2.0 | 2.0-fast | 2.0-mini | 1.5-pro
image_model = "5.0"    # 5.0-pro | 5.0 | 5.0-lite
resolution = "720p"    # 480p | 720p | 1080p | 4k   (fast/mini are 720p max)
ratio = "9:16"
duration = 5
generate_audio = true
watermark = false
output_dir = "out"
"""
