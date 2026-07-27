# seedance-batch

Batch image + video generation on ByteDance's **Seedance 2.0** (video) and **Seedream 5.0**
(images), through the official **BytePlus ModelArk** API.

One API key drives both. Point it at a spreadsheet, walk away, come back to a folder of
finished MP4s and PNGs.

```
seedance batch jobs.csv
```

## Why this exists

Seedance 2.0 currently sits at **#2 on the Artificial Analysis leaderboards for both
text-to-video and image-to-video** — above Veo 3.1 and Kling 3.0. It generates **synchronised
audio natively** and takes up to 9 images, 3 videos and 3 audio clips as references in a
single call. The one thing it has no official answer for is running a few hundred of them
without babysitting a browser tab. That's this.

## Install

```bash
pip install httpx          # the only hard dependency
git clone <this repo> && cd seedance-batch
python -m pip install -e .
```

Python 3.9+. On 3.11+ config parsing uses the stdlib `tomllib`; below that a small built-in
parser handles the config format, so there is nothing else to install.

## Setup

```bash
seedance init                 # writes seedance.toml
export ARK_API_KEY=your_key   # or paste it into the file
seedance check                # verifies the key and lists available models
```

Get a key from the [BytePlus ModelArk console](https://console.byteplus.com/ark/region:ark+ap-southeast-1/apikey).
Signup is email + an international card — no Chinese phone number, and ModelArk is available
in 190+ countries. (The China-side Volcengine Ark API is cheaper but needs a Chinese national
ID or business licence, so it is not a realistic route from outside China.)

`seedance check` output:

```
Credentials:
  byteplus-main    byteplus     conc=3  key=ark-…3456
  → 1 credential(s), 3 concurrent generation slots

Probing each key against BytePlus ModelArk…
  ✓ byteplus-main: reachable, authenticated
```

## Usage

### One-off

```bash
seedance video "a welder in a workshop, sparks flying, shallow depth of field" \
  --duration 10 --ratio 9:16 --resolution 720p --camera-fixed

seedance image "portrait of a carpenter, studio lighting" -n 4 --size 2K
```

### Batch from a spreadsheet

Save a CSV with a `prompt` column. Everything else is optional and falls back to the
`[defaults]` in your config.

```csv
id,kind,prompt,duration,ratio,resolution,camera_fixed,first_frame,tag
r1,video,"welder in a workshop, sparks",5,9:16,720p,true,,welder
r2,video,"electrician on a ladder, golden hour",10,9:16,720p,false,,electrician
r3,image,"portrait of a carpenter, studio light",,,,,,carpenter
r4,video,"animate this still",5,9:16,720p,,https://…/frame.jpg,animated
```

```bash
seedance batch jobs.csv
```

Recognised columns: `id`, `kind` (video|image), `prompt`, `model`, `duration`, `ratio`,
`resolution`, `seed`, `audio`, `watermark`, `camera_fixed`, `n` (images per prompt),
`size`, `tag`, and the reference columns `first_frame`, `last_frame`, `ref_image`,
`ref_video`, `ref_audio` (pipe-separate several URLs in one cell).

JSON and JSONL job files work too.

### Sequences longer than 15 seconds

A single generation caps at 15s. `chain` gets around that by feeding each clip's final frame
in as the next clip's first frame, so the result is continuous rather than a hard cut:

```bash
seedance chain script.txt --duration 10 --ratio 9:16
```

`script.txt` is one prompt per line — five lines at 10s gives you a continuous 50-second
sequence.

### Checking a run

```bash
seedance status out/ledger.jsonl
```

## What it handles for you

**Concurrency is the real ceiling, not request rate.** A BytePlus *individual* key allows 180
requests/minute but only **3 concurrent** video tasks (enterprise keys get 10). The runner
tracks in-flight jobs per key and keeps every slot busy without tripping the limit. If you
legitimately hold more than one key, list them all and work spreads across them automatically.

**Resume is free.** Every state change is appended to a JSONL ledger. Kill the process, close
the laptop, lose the wifi — re-run the identical command and it skips everything already
finished and retries only what didn't.

**Validation happens before submission.** Model limits are enforced client-side, so a row
asking for 1080p from the `fast` model or a 30-second clip is rejected for free rather than
after the API charges you. Rejected rows are always printed and always change the exit code —
they are never silently dropped.

**Failures are handled per-key.** A revoked key gets disabled, an out-of-credit key gets
parked, a 429 gets retried with jittered backoff, and work reroutes to whatever is healthy.
A generation that succeeds but fails to download keeps its URL and is never marked failed —
you already paid for it.

**Cost comes from the provider's own `usage` block.** Nothing here estimates a price.

## Models

| Alias | Model ID | Resolutions | Duration |
|---|---|---|---|
| `2.0` | `dreamina-seedance-2-0-260128` | 480p, 720p, 1080p, 4K (10-bit) | 4–15s |
| `2.0-fast` | `dreamina-seedance-2-0-fast-260128` | 480p, 720p | 4–15s |
| `2.0-mini` | `dreamina-seedance-2-0-mini-260615` | 480p, 720p | 4–15s |
| `1.5-pro` | `seedance-1-5-pro-251215` | 480p, 720p, 1080p | 4–12s |

Images: `5.0-pro`, `5.0`, `5.0-lite` (Seedream), plus `4.5` and `4.0`.

All Seedance 2.0 variants do text-to-video, image-to-video (first frame, or first *and* last),
multi-reference-to-video, video modification, video extension, and synchronised audio, at 24fps.

Only the full `2.0` model does 1080p and 4K — **`fast` and `mini` top out at 720p**, which
several third-party guides get wrong. 4K is separately throttled to 1 concurrent task.

**Seedance 2.5** (announced 2026-06-23: 30s native, 50 reference inputs, native 4K) is not in
the BytePlus international model list yet — it is China-first via Jimeng/Doubao. Model IDs
live in one dict in `client.py`, so adding it is a one-line change when it appears.

## A note on "bulk account" tools

If you came here after seeing tools that mass-create accounts to farm free credits: the
arithmetic doesn't work, and this tool deliberately doesn't do it.

BytePlus gives new accounts about 2 million free tokens per vision model, which is roughly
**six seconds** of 720p video. Dreamina's free tier is ~225 credits/day — one or two
*watermarked* clips. Meanwhile the credential side costs real money and recurs: an antidetect
browser seat, a residential IP per account (bandwidth alone runs a few dollars a month per
account), and phone numbers that stop working after ~15 uses. Multilogin — a company that
sells the tooling — publishes a **40% success rate** for creating one Google account, so every
durable account takes about 2.5 attempts before the recurring costs even start.

That is more per usable clip than simply paying, and the output is watermarked and capped at
720p. The failure mode is also asymmetric: platforms link accounts by device fingerprint,
IP reputation and payment fingerprint, and a ban commonly takes the whole identity with it.
When mass farming has succeeded at scale, the outcome was Midjourney's — the free tier was
removed permanently, for everyone.

Pooling API keys you actually hold, which this tool does support, is ordinary client-side rate
management and a different thing entirely.

## Development

```bash
python tests/test_pipeline.py
```

18 end-to-end tests run against a mock ModelArk server (`tests/mock_ark.py`) that reproduces
the documented wire format plus the failure modes — 429 throttling, quota exhaustion, revoked
keys, slow tasks. No API key or network access needed.
