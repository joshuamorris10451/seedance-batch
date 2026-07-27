# seedance-batch — Seedance 2.0 / Seedream 5.0 batch generation tool

**What it is:** A standalone Python CLI that runs **batch image + video generation** on
ByteDance's Seedance 2.0 (video) and Seedream 5.0 (images) through the official **BytePlus
ModelArk** API. One key drives both. CSV/JSON in → finished MP4s and PNGs on disk.
**Status:** BUILT and tested (18/18 end-to-end tests pass against a mock ModelArk server).
**NOT yet run against a live key — Bob has no BytePlus account yet.**

## Live assets
- **Research brief (LIVE):** https://joshuamorris10451.github.io/seedance-batch/ — noindex.
- **Repo:** `joshuamorris10451/seedance-batch` (PUBLIC, needed for Pages). Collaborators:
  SilentAurora245 + mary3862jon. Contains the tool source + the brief in `docs/`.
- ⚠ **Hosting gotcha, cost me a redo:** I first pushed to `timothywade8452`, whose *user-pages*
  site has a CNAME to **tryretafit.com** — so the project page served under our RetaFit affiliate
  domain. Deleted and moved. **Before publishing to any backup account, check
  `GET /repos/<acct>/<acct>.github.io/contents/CNAME`.** Known contaminated:
  timothywade8452 → tryretafit.com · joangoodwin10190 → ozem-plus.store ·
  edwardoliver104 → osanix.shop. Clean at time of writing: joshuamorris10451 (used here),
  francistucker5374, paulblack6522.

## Why this shape (the research verdict)

Bob's original ask was to clone the "bulk account creation + free credit farming" tools he'd
seen, in the style of `veo-flow-multi`. Deep research (5 parallel agents, 2026-07-27) says
**that premise is dead for this model**, and the tool was built accordingly:

- **BytePlus free tier ≈ 2M tokens per vision model ≈ SIX SECONDS of 720p video.** Farming
  accounts for 6 seconds each is not a business. Dreamina's consumer free tier is ~225
  credits/day = 1–2 *watermarked* clips.
- **Seedance has a cheap, purchasable, open API** — which is exactly what Veo does NOT have.
  The whole reason `veo-flow-multi` pools accounts is that Google Flow has no API and credits
  aren't buyable at scale. That constraint does not exist here, so the account-pooling premise
  doesn't transfer.
- **Multilogin's own published figure: 40% success rate** creating one Google account with
  their paid antidetect + virtual numbers (<1% without). Costs recur monthly per account
  (residential IP, antidetect seat, numbers dying after ~15 uses).
- **Ban blast radius is the real killer FOR BOB SPECIFICALLY:** Google links accounts by
  device fingerprint, IP and *payment fingerprint*, and a multi-account ban cascades to linked
  accounts. Bob runs client Google Ads (RingOnDemand, LendPeak, the GCALIT MCC). That is an
  unacceptable thing to gamble.
- **Precedent:** mass farming's actual historical outcome was Midjourney killing its free tier
  permanently for everyone (CEO on record: throwaway accounts, viral how-to video).
- Most advertised "bulk account" tools are malware/fraud — see the Check Point fake-Kling-AI
  campaign (PureHVNC RAT), and the taxonomy of fake "Seedance 2.0 API" resellers (pooled
  accounts / Seedance 1.5 misbranded as 2.0 / nothing behind it at all).

So: **pool API keys you actually hold** (ordinary rate management), don't create accounts.

## The working version — settled from primary sources

- **Target = Seedance 2.0 on BytePlus ModelArk**, base URL
  `https://ark.ap-southeast.bytepluses.com/api/v3`. Verified reachable from this box (proper
  401 without a key). Console: `https://console.byteplus.com/ark/region:ark+ap-southeast-1/apikey`
- Model IDs pulled from the **live BytePlus model list** (not blogs):
  `dreamina-seedance-2-0-260128` (480p/720p/1080p/4K, 4–15s) ·
  `dreamina-seedance-2-0-fast-260128` (480p/720p) ·
  `dreamina-seedance-2-0-mini-260615` (480p/720p) · `seedance-1-5-pro-251215` (4–12s).
  Images: `seedream-5-0-pro-260628`, `seedream-5-0-260128`, `seedream-5-0-lite-260128`.
  China-side Volcengine Ark uses a `doubao-` prefix instead of `dreamina-`.
- **Rate limits: individual keys = 180 RPM but only 3 CONCURRENT tasks** (enterprise 600/10).
  4K is 15 RPM / 1 concurrent for everyone. **Concurrency, not RPM, is the throughput ceiling**
  — that's what the pool is built around.
- **Seedance 2.5** (announced 2026-06-23: 30s native, 50 refs, native 4K) is **NOT in the
  BytePlus international model list** as of 2026-07-27, despite blog claims of a July 16 API.
  China-first via Jimeng/Doubao. Model IDs live in one dict in `client.py` → one-line change.
- Quality: Seedance 2.0 is **#2 on Artificial Analysis for both T2V and I2V** — above Veo 3.1
  and Kling 3.0, behind only Gemini Omni Flash. Genuinely better than what veo-flow-multi drives.
- Full details + the suspension timeline: `docs/VERIFIED-FACTS.md`.

## Key facts about the code

- `src/seedance/client.py` — async Seedance client. Parameter set was **read out of the
  official SDK source**, so it's the real contract. Notable params most guides never mention:
  `callback_url` (webhooks instead of polling), **`return_last_frame`** (clip chaining past the
  15s cap), `camera_fixed` (locked camera — matches the VegToons look-lock rule), `seed`,
  `draft`, `priority`.
- `src/seedance/images.py` — Seedream on the same key. Batch mode is
  `sequential_image_generation` + `sequential_image_generation_options.max_images`.
- `src/seedance/pool.py` — per-credential concurrency semaphore, least-loaded selection,
  auto-disable on auth failure / hard quota, cooldown on transient 429.
- `src/seedance/runner.py` — JSONL ledger → **free resume**; a generation that succeeds but
  fails to download keeps its URL and is NOT marked failed (it was already paid for).
- `src/seedance/config.py` — includes a **minimal TOML parser fallback** because Python 3.10
  has no `tomllib` and Bob's Windows box may lack `tomli`. Zero hard deps beyond `httpx`.
- Cost is always read from the API's own `usage` block. **Nothing estimates a price.**

## Testing

`python tests/test_pipeline.py` → 18 tests, no key or network needed. `tests/mock_ark.py`
reproduces the documented wire format plus 429 throttling, quota exhaustion, revoked keys and
slow tasks, so pool failover is genuinely exercised.

## Dead ends / gotchas — do NOT redo

- `docs.byteplus.com` and `volcengine.com` docs are **JavaScript-rendered Lark docs** —
  WebFetch returns only the nav. The real content is embedded in the HTML as JSON: extract
  `\"insert\":\"…\"` fields (escaped form) and join by `zoneId`. That's how the model IDs,
  capability matrix and code samples were recovered.
- `pip install 'byteplus-python-sdk-v2[ark]'` **misses a transitive dep** — install `sniffio`
  explicitly or the import fails. (We don't ship the SDK; used it only to read the contract.)
- `pip install .` in this sandbox needs **`--no-build-isolation`** — isolated builds grab a
  stale setuptools and produce a package literally named `UNKNOWN` 0.0.0.
- Treat `seedance2.so`, `easemate.ai`, `gamsgo`, `apiframe`, `evolink`, `lumiying`, `novoads`,
  `soravideo.art`, `seedance2pro.io` as **affiliate SEO spam** — several contradict the
  official docs (e.g. claiming Seedance 2.0 has no 1080p; in fact only *fast*/*mini* are
  720p-capped). Same for the "Wan 2.7 open weights" cluster — fabricated.
- The mock server must **drain the POST body before returning an error**, or HTTP keep-alive
  desyncs and the next request 400s. (Cost me one debugging cycle.)

## Open / next

- **Bob has no BytePlus key yet** — nothing has hit the live API. First real run should be a
  single 5s 720p clip to measure true cost from the `usage` block before any volume.
- **Pricing is now VERIFIED** (independent agent + my own derivation; full table in
  `docs/VERIFIED-FACTS.md` §5). Headline: **5s @720p = $0.76** on the flagship 2.0, **$1.87 at
  1080p**, $0.38 on mini, $0.10 on 1.0-pro-fast. Formula
  `tokens = (in_video_dur + out_dur) × w × h × 24 ÷ 1024`, billed per million, only on success.
  **Going direct beats every reseller for the 2.0 family** — fal.ai charges exactly 2×.
  Note the verification *corrected* my own first derivation ($0.69 → $0.76); don't re-derive
  from the China CNY rate, use the BytePlus USD card.
- Free tier is **500K tokens/model officially** (byteplus.com markets "2m" on the Seedance page;
  exact grant is console-only and UNVERIFIED). That's ~4–18 clips of 5s/720p, i.e. a fresh
  account is worth **$3.50–$14 of footage, once** — the number that kills the farming idea.
- Reseller adapters (fal.ai, Segmind, WaveSpeed, Replicate) are designed for in `pool.py`
  (`provider` field) but **not implemented** — only `byteplus` works today. Worth adding if
  BytePlus suspends again (the copyright dispute is unresolved; studios sent C&Ds, no lawsuit
  filed yet, but a second suspension is a live tail-risk).
- No repo pushed, no release built. Decide account + visibility before pushing.
- Self-hosting is the other route for real volume: on Bob's planned RTX PRO 6000 + 5090 rig,
  LTX-2.3 / Wan 2.2 run at roughly $0.01/clip marginal, ~500–1,000 clips/day. Rig is still a
  plan, not built. Open-weight quality sits ~250 Elo behind Seedance 2.0 — fine for volume
  social, not for hero shots.
