"""Command-line interface.

    seedance init                      write an example seedance.toml
    seedance check                     verify keys, show concurrency, list models
    seedance video "prompt" [...]      one-off video
    seedance image "prompt" [...]      one-off image
    seedance batch jobs.csv            batch run from a spreadsheet
    seedance chain script.txt          continuous multi-clip sequence
    seedance status                    summarise a run ledger
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import pathlib
import sys
from typing import Any, Sequence

from .client import (
    ARK_INTERNATIONAL,
    MODEL_LIMITS,
    MODELS,
    AuthFailed,
    Reference,
    SeedanceClient,
    SeedanceError,
    VideoJob,
)
from .config import EXAMPLE_CONFIG, describe_credentials, load_config, load_credentials
from .images import IMAGE_MODELS, ImageJob
from .pool import CredentialPool
from .providers import FAL_QUEUE, FalClient
from .runner import BatchRunner, Ledger, LedgerEntry

log = logging.getLogger("seedance")

TRUTHY = {"1", "true", "yes", "y", "on"}


def _resolve_model(name: str) -> str:
    return MODELS.get(name, name)


def _resolve_image_model(name: str) -> str:
    return IMAGE_MODELS.get(name, name)


_RES_ORDER = {"480p": 0, "720p": 1, "1080p": 2, "4k": 3}


def _by_size(resolutions: Any) -> list[str]:
    """Smallest-first, so the list reads as a ladder rather than alphabetically."""
    return sorted(resolutions, key=lambda r: _RES_ORDER.get(r.lower(), 99))


def _bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUTHY


def _progress(entry: LedgerEntry) -> None:
    icon = {"running": "·", "succeeded": "✓", "failed": "✗"}.get(entry.status, "?")
    label = entry.tag or entry.job_id
    if entry.status == "running":
        print(f"  {icon} {label} … ({entry.credential or 'pending'})", flush=True)
    elif entry.status == "succeeded":
        where = entry.files[0] if entry.files else (entry.output[0] if entry.output else "")
        print(f"  {icon} {label} → {where}", flush=True)
    else:
        print(f"  {icon} {label} FAILED: {entry.error}", flush=True)


def _build_pool(args: argparse.Namespace) -> tuple[CredentialPool, dict[str, Any]]:
    config = load_config(pathlib.Path(args.config) if args.config else None)
    creds = load_credentials(config)
    if not creds:
        print(
            "No API keys found.\n\n"
            "Set one up with:\n"
            "  1. Get a key: https://console.byteplus.com/ark/region:ark+ap-southeast-1/apikey\n"
            "  2. export ARK_API_KEY=your_key\n"
            "     (or run `seedance init` and fill in seedance.toml)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return CredentialPool(creds), config


def _defaults(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("defaults", {}) or {}


def _video_from_row(row: dict[str, str], defaults: dict[str, Any]) -> VideoJob:
    refs: list[Reference] = []
    for col, kind, role in (
        ("first_frame", "image", "first_frame"),
        ("last_frame", "image", "last_frame"),
        ("ref_image", "image", "reference_image"),
        ("ref_video", "video", "reference_video"),
        ("ref_audio", "audio", "reference_audio"),
    ):
        raw = (row.get(col) or "").strip()
        if not raw:
            continue
        for url in [u.strip() for u in raw.split("|") if u.strip()]:
            refs.append(Reference(url=url, kind=kind, role=role))

    seed = (row.get("seed") or "").strip()
    return VideoJob(
        prompt=row["prompt"],
        model=_resolve_model(row.get("model") or defaults.get("model", "2.0")),
        duration=int(row.get("duration") or defaults.get("duration", 5)),
        ratio=row.get("ratio") or defaults.get("ratio", "9:16"),
        resolution=row.get("resolution") or defaults.get("resolution", "720p"),
        generate_audio=_bool(row.get("audio"), bool(defaults.get("generate_audio", True))),
        watermark=_bool(row.get("watermark"), bool(defaults.get("watermark", False))),
        camera_fixed=_bool(row.get("camera_fixed"), False),
        seed=int(seed) if seed else None,
        references=refs,
        tag=row.get("tag") or row.get("id") or None,
    )


def _image_from_row(row: dict[str, str], defaults: dict[str, Any]) -> ImageJob:
    refs = [u.strip() for u in (row.get("ref_image") or "").split("|") if u.strip()]
    seed = (row.get("seed") or "").strip()
    return ImageJob(
        prompt=row["prompt"],
        model=_resolve_image_model(row.get("model") or defaults.get("image_model", "5.0")),
        size=row.get("size") or defaults.get("size", "2K"),
        n=int(row.get("n") or 1),
        reference_urls=refs,
        seed=int(seed) if seed else None,
        watermark=_bool(row.get("watermark"), bool(defaults.get("watermark", False))),
        tag=row.get("tag") or row.get("id") or None,
    )


async def cmd_check(args: argparse.Namespace) -> int:
    pool, config = _build_pool(args)
    print(f"config: {config.get('_source', 'environment only')}\n")
    print("Credentials:")
    print(describe_credentials(pool.credentials))
    print("\nProbing each key against its own provider…")

    ok = 0
    for cred in pool.credentials:
        # Probe per provider. A fal key checked against the BytePlus endpoint returns 401
        # and reads as "dead key", which is exactly the wrong thing to tell someone whose
        # key is fine.
        try:
            if cred.provider == "fal":
                async with FalClient(
                    cred.api_key, base_url=cred.base_url or FAL_QUEUE, max_retries=0
                ) as fclient:
                    # No list endpoint on the queue API; a HEAD-ish GET on a request id
                    # that cannot exist distinguishes 401 (bad key) from 404 (key fine).
                    try:
                        await fclient._request(
                            "GET",
                            f"{fclient.base_url}/fal-ai/any/requests/"
                            f"00000000000000000000000000000000/status",
                        )
                    except SeedanceError as exc:
                        if isinstance(exc, AuthFailed):
                            raise
                        # Anything that is not an auth failure means the key authenticated.
            else:
                async with SeedanceClient(
                    cred.api_key, base_url=cred.base_url or ARK_INTERNATIONAL, max_retries=0
                ) as client:
                    await client._request("GET", "/contents/generations/tasks?page_size=1")
        except Exception as exc:
            print(f"  ✗ {cred.name} ({cred.provider}): {exc}")
            continue
        print(f"  ✓ {cred.name} ({cred.provider}): reachable, authenticated")
        ok += 1

    print(f"\n{ok}/{len(pool.credentials)} key(s) working.")
    print("\nVideo models:")
    for alias, mid in MODELS.items():
        limits = MODEL_LIMITS.get(mid)
        if limits:
            lo, hi = limits["duration"]
            res = ", ".join(_by_size(limits["resolutions"]))
            print(f"  {alias:<14} {mid:<36} {lo}-{hi}s  [{res}]")
        else:
            print(f"  {alias:<14} {mid}")
    print("\nImage models:")
    for alias, mid in IMAGE_MODELS.items():
        print(f"  {alias:<14} {mid}")
    return 0 if ok else 1


async def cmd_video(args: argparse.Namespace) -> int:
    pool, config = _build_pool(args)
    defaults = _defaults(config)
    refs = [Reference(url=u, kind="image", role="first_frame") for u in (args.first_frame or [])]
    refs += [Reference(url=u, kind="image", role="reference_image") for u in (args.ref_image or [])]
    refs += [Reference(url=u, kind="video", role="reference_video") for u in (args.ref_video or [])]
    refs += [Reference(url=u, kind="audio", role="reference_audio") for u in (args.ref_audio or [])]

    job = VideoJob(
        prompt=args.prompt,
        model=_resolve_model(args.model or defaults.get("model", "2.0")),
        duration=args.duration or int(defaults.get("duration", 5)),
        ratio=args.ratio or defaults.get("ratio", "9:16"),
        resolution=args.resolution or defaults.get("resolution", "720p"),
        generate_audio=not args.no_audio,
        watermark=args.watermark,
        camera_fixed=args.camera_fixed,
        seed=args.seed,
        references=refs,
        tag="cli",
    )
    runner = BatchRunner(
        pool,
        output_dir=pathlib.Path(args.output or defaults.get("output_dir", "out")),
        progress=_progress,
    )
    entry = await runner._run_video("cli_video", job)
    return 0 if entry.status == "succeeded" else 1


async def cmd_image(args: argparse.Namespace) -> int:
    pool, config = _build_pool(args)
    defaults = _defaults(config)
    job = ImageJob(
        prompt=args.prompt,
        model=_resolve_image_model(args.model or defaults.get("image_model", "5.0")),
        size=args.size or defaults.get("size", "2K"),
        n=args.n,
        reference_urls=args.ref_image or [],
        seed=args.seed,
        watermark=args.watermark,
        tag="cli",
    )
    runner = BatchRunner(
        pool,
        output_dir=pathlib.Path(args.output or defaults.get("output_dir", "out")),
        progress=_progress,
    )
    entry = await runner._run_image("cli_image", job)
    return 0 if entry.status == "succeeded" else 1


def _read_jobs(path: pathlib.Path) -> list[dict[str, str]]:
    if path.suffix.lower() in {".json", ".jsonl"}:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("jobs", [])
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


async def cmd_batch(args: argparse.Namespace) -> int:
    pool, config = _build_pool(args)
    defaults = _defaults(config)
    path = pathlib.Path(args.jobs)
    if not path.exists():
        print(f"job file not found: {path}", file=sys.stderr)
        return 2

    rows = _read_jobs(path)
    rows = [r for r in rows if (r.get("prompt") or "").strip()]
    if not rows:
        print("no rows with a `prompt` column", file=sys.stderr)
        return 2

    jobs: list[tuple[str, VideoJob | ImageJob]] = []
    invalid: list[tuple[str, str]] = []
    for idx, row in enumerate(rows):
        kind = (row.get("kind") or args.kind or "video").strip().lower()
        jid = (row.get("id") or f"{kind}_{idx:04d}").strip()
        try:
            job = _video_from_row(row, defaults) if kind == "video" else _image_from_row(row, defaults)
            if isinstance(job, VideoJob):
                job.validate()
        except (ValueError, KeyError) as exc:
            invalid.append((f"row {idx + 1} ({jid})", str(exc)))
            if args.strict:
                print(f"✗ {invalid[-1][0]}: {exc}", file=sys.stderr)
                print("aborting (--strict)", file=sys.stderr)
                return 2
            continue
        jobs.append((jid, job))

    # Never let dropped rows go unmentioned — a silent skip reads as "everything ran".
    if invalid:
        print(f"⚠ {len(invalid)} row(s) rejected before submission (nothing was charged):")
        for where, why in invalid:
            print(f"    {where}: {why}")
        print()

    if not jobs:
        print("no valid rows to run.", file=sys.stderr)
        return 2

    out = pathlib.Path(args.output or defaults.get("output_dir", "out"))
    runner = BatchRunner(pool, output_dir=out, progress=_progress)

    print(f"{len(jobs)} job(s) · {pool.total_concurrency} concurrent slot(s) · output → {out}\n")
    entries = await runner.run(jobs, resume=not args.no_resume)

    ok = sum(1 for e in entries if e.status == "succeeded")
    failed = [e for e in entries if e.status != "succeeded"]
    print(f"\n{ok}/{len(entries)} succeeded.", end="")
    print(f"  ({len(invalid)} rejected before submission)" if invalid else "")
    if failed:
        print("Failed:")
        for e in failed:
            print(f"  {e.tag or e.job_id}: {e.error}")
        print("\nRe-run the same command to retry only the failures.")
    print(f"Ledger: {runner.ledger.path}")
    return 0 if not failed and not invalid else 1


async def cmd_chain(args: argparse.Namespace) -> int:
    pool, config = _build_pool(args)
    defaults = _defaults(config)
    path = pathlib.Path(args.script)
    if not path.exists():
        print(f"script not found: {path}", file=sys.stderr)
        return 2
    prompts = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not prompts:
        print("script is empty", file=sys.stderr)
        return 2

    base = VideoJob(
        prompt="",
        model=_resolve_model(args.model or defaults.get("model", "2.0")),
        duration=args.duration or int(defaults.get("duration", 5)),
        ratio=args.ratio or defaults.get("ratio", "9:16"),
        resolution=args.resolution or defaults.get("resolution", "720p"),
        generate_audio=not args.no_audio,
        watermark=args.watermark,
        camera_fixed=args.camera_fixed,
    )
    runner = BatchRunner(
        pool,
        output_dir=pathlib.Path(args.output or defaults.get("output_dir", "out")),
        progress=_progress,
    )
    print(f"chaining {len(prompts)} clip(s) ≈ {len(prompts) * base.duration}s total\n")
    entries = await runner.chain(prompts, base=base)
    ok = sum(1 for e in entries if e.status == "succeeded")
    print(f"\n{ok}/{len(prompts)} clip(s) generated.")
    return 0 if ok == len(prompts) else 1


def cmd_init(args: argparse.Namespace) -> int:
    dest = pathlib.Path(args.path or "seedance.toml")
    if dest.exists() and not args.force:
        print(f"{dest} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    dest.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"wrote {dest}")
    print("Next: put your BytePlus key in it, then run `seedance check`.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.ledger or "out/ledger.jsonl")
    if not path.exists():
        print(f"no ledger at {path}", file=sys.stderr)
        return 1
    ledger = Ledger(path)
    summary = ledger.summary()
    total = sum(summary.values())
    print(f"{path}: {total} job(s)")
    for status, count in sorted(summary.items()):
        print(f"  {status:<12} {count}")
    tokens = sum(
        int(e.usage.get("total_tokens") or 0)
        for e in ledger._state.values()
        if e.usage
    )
    if tokens:
        print(f"\nreported usage: {tokens:,} tokens")
        print("(cost = tokens x your BytePlus rate; the API reports usage, we never estimate)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seedance",
        description="Batch image + video generation on ByteDance Seedance / Seedream.",
    )
    p.add_argument("--config", help="path to seedance.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="write an example config file")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init, is_async=False)

    sp = sub.add_parser("check", help="verify keys and list models")
    sp.set_defaults(func=cmd_check, is_async=True)

    sp = sub.add_parser("video", help="generate one video")
    sp.add_argument("prompt")
    sp.add_argument("--model")
    sp.add_argument("--duration", type=int)
    sp.add_argument("--ratio")
    sp.add_argument("--resolution")
    sp.add_argument("--seed", type=int)
    sp.add_argument("--first-frame", action="append")
    sp.add_argument("--ref-image", action="append")
    sp.add_argument("--ref-video", action="append")
    sp.add_argument("--ref-audio", action="append")
    sp.add_argument("--no-audio", action="store_true")
    sp.add_argument("--watermark", action="store_true")
    sp.add_argument("--camera-fixed", action="store_true")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_video, is_async=True)

    sp = sub.add_parser("image", help="generate image(s)")
    sp.add_argument("prompt")
    sp.add_argument("--model")
    sp.add_argument("--size")
    sp.add_argument("-n", type=int, default=1)
    sp.add_argument("--ref-image", action="append")
    sp.add_argument("--seed", type=int)
    sp.add_argument("--watermark", action="store_true")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_image, is_async=True)

    sp = sub.add_parser("batch", help="run a CSV/JSON job list")
    sp.add_argument("jobs")
    sp.add_argument("--kind", choices=["video", "image"], help="default when no kind column")
    sp.add_argument("--no-resume", action="store_true", help="re-run already-succeeded jobs")
    sp.add_argument("--strict", action="store_true", help="abort on the first invalid row")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_batch, is_async=True)

    sp = sub.add_parser("chain", help="continuous sequence, one prompt per line")
    sp.add_argument("script")
    sp.add_argument("--model")
    sp.add_argument("--duration", type=int)
    sp.add_argument("--ratio")
    sp.add_argument("--resolution")
    sp.add_argument("--no-audio", action="store_true")
    sp.add_argument("--watermark", action="store_true")
    sp.add_argument("--camera-fixed", action="store_true")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_chain, is_async=True)

    sp = sub.add_parser("status", help="summarise a run ledger")
    sp.add_argument("ledger", nargs="?")
    sp.set_defaults(func=cmd_status, is_async=False)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if getattr(args, "is_async", False):
            return asyncio.run(args.func(args))
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted — re-run the same command to resume", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
