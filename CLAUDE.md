# seedance-batch — Seedance 2.0 / Seedream 5.0 batch generation tool

**What it is:** A standalone Python CLI that runs **batch image + video generation** on
ByteDance's Seedance 2.0 (video) and Seedream 5.0 (images) through the official **BytePlus
ModelArk** API. One key drives both. CSV/JSON in → finished MP4s and PNGs on disk.
**Status:** BUILT and tested (18/18 end-to-end tests pass against a mock ModelArk server).
**NOT yet run against a live key — Bob has no BytePlus account yet.**

## Live assets
- **Research brief (LIVE):** https://joshuamorris10451.github.io/seedance-batch/ — noindex.
- **VA QA task (LIVE, 2026-07-27):** https://joshuamorris10451.github.io/seedance-qa/ — noindex,
  **deliberately a separate repo** (`joshuamorris10451/seedance-qa`) so the URL is not one
  keystroke from the internal brief. Contains only her task: one fal signup, run the 9-test
  matrix, send back videos + ledger + a filled results table. Says explicitly **one account
  only, and check with Bob if anyone asks for more**.
- ⚠ Scrubbed client names (RingOnDemand / LendPeak / GCALIT MCC) out of the public brief on
  2026-07-27 — they were on a public page and a VA was about to be pointed at the project.
  **Don't put client names back on anything published.**
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
- `src/seedance/client.py` poll cap: `wait()` backs off ×1.25 to a **cap of 8s, not 20s**
  (changed 2026-07-27). A finished task holds one of the key's 3 slots until the poller notices,
  so a long interval is throughput thrown away: at the old 20s cap a 90s generation was detected
  17.6s late = **19.5% of the slot wasted**; at 8s the worst case is 4.6%. Costs 22 req/min/key
  against a 180 RPM allowance. Guarded by a virtual-clock check in `scale_test.py` that fails
  above 6% — confirmed to actually fail at the old cap, so it isn't a rubber stamp.
- `src/seedance/config.py` — includes a **minimal TOML parser fallback** because Python 3.10
  has no `tomllib` and Bob's Windows box may lack `tomli`. Zero hard deps beyond `httpx`.
- Cost is always read from the API's own `usage` block. **Nothing estimates a price.**

## Testing

`python3 tests/test_pipeline.py` → 18 tests, no key or network needed. `tests/mock_ark.py`
reproduces the documented wire format plus 429 throttling, quota exhaustion, revoked keys and
slow tasks, so pool failover is genuinely exercised.

`python3 tests/test_fal.py` → 24 tests for the fal adapter against a mock of fal's queue,
including the collapsed-polling-path trap, the `Key` vs `Bearer` header, dead-key failover, a
402 parking the key, and `chain()` refusing on a fal-only pool.

`python3 tests/scale_test.py` → **throughput harness, added 2026-07-27**. Answers "how far can
batch generation go" without a key or an account. Its mock (unlike `mock_ark.py`) **enforces the
3-concurrent-per-key cap**, rejecting a 4th simultaneous task with a 429, because concurrency is
the real ceiling. Result: 1→16 keys, 12→192 jobs, **every job completed, pool saturated every
slot (peak == keys×3), zero server rejections** — the client self-limits rather than firing and
getting bounced. Throughput is linear in key count; there is no client-side cleverness available.

⚠ **Do NOT read the "% of theoretical" column as a tool finding.** It falls 77%→39% as keys rise,
but a control run (16 keys, generation time 1s→8s at constant concurrency) recovers it to 72% —
that ceiling is the Python `ThreadingHTTPServer` saturating on request rate, not the runner.
Also: the mock must be `ThreadingHTTPServer`; plain `HTTPServer` serialises the keep-alive
connections the runner holds per in-flight job and stalls the whole run.

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

- **Nothing has hit a live API yet** — no BytePlus key, no fal key. Everything below is proven
  against mocks only. The first real run is the VA's 9-test matrix
  (`examples/fal-trial-matrix.csv`, **$9.07 of a $10 fal trial credit**): model tiers,
  resolutions, ratios, 10s duration, audio toggle, image-to-video. Every row was validated
  through the adapter and the whole batch driven end-to-end against a mock of fal's queue, so
  the commands on the QA page are proven, not guessed.
- **Bob asked for a VA process to create 5-10 BytePlus accounts; I declined and built the
  legitimate test instead.** Reason recorded so it isn't relitigated: the free quota needs
  **identity-verified** accounts and BytePlus collapses same-phone/same-ID accounts into one
  user, so 5-10 accounts means 5-10 identity documents, with the **VA's** name and number on
  them, for ~$94 of credit total. The testing goal needs none of it — `scale_test.py` answers
  throughput for free at any scale, and live-API validation needs exactly one key.
- **Pricing is now VERIFIED** (independent agent + my own derivation; full table in
  `docs/VERIFIED-FACTS.md` §5). Headline: **5s @720p = $0.76** on the flagship 2.0, **$1.87 at
  1080p**, $0.38 on mini, $0.10 on 1.0-pro-fast. Formula
  `tokens = (in_video_dur + out_dur) × w × h × 24 ÷ 1024`, billed per million, only on success.
  **Going direct beats every reseller for the 2.0 family** — fal.ai charges exactly 2×.
  Note the verification *corrected* my own first derivation ($0.69 → $0.76); don't re-derive
  from the China CNY rate, use the BytePlus USD card.
- Free tier **RESOLVED 2026-07-27** (Bob pushed back; he was right). Grant is **500K tokens PER
  MODEL**, counted separately per model, shared across sub-accounts — so the marketed "2m" is
  500K × several models. **24 clips of 5s/720p per verified account = $9.44, once** (60 clips at
  480p). Requires **identity verification**; same phone / same ID doc / same account ID are
  explicitly "the same user", and "bulk abuse" is a named disqualifier. Sources + table in
  `docs/VERIFIED-FACTS.md` §5.
- ⚠ **I got one argument wrong and it's now corrected on the live page:** the brief claimed
  farming risked Bob's client Google Ads via ban cascade. That's the *Veo/Flow* risk model —
  BytePlus is ByteDance and cannot touch a Google account. Don't repeat it.
- **Reseller signup credits are the easier free door**, and are what rival "Seedance batch tool"
  sellers actually advertise as a 5–10 video trial: **fal.ai $10, no credit card** (~6 clips at
  720p), WaveSpeed/Replicate smaller, Higgsfield 24h promos — email only, **no ID check**. They
  are not farming; it's their own paid credit as a lead magnet for a subscription.
- **fal.ai adapter is BUILT** (`src/seedance/providers.py`, 2026-07-27). Segmind / WaveSpeed /
  Replicate still are not — `pool.py`'s `provider` field is the extension point. Worth adding if
  BytePlus suspends again (the copyright dispute is unresolved; studios sent C&Ds, no lawsuit
  filed yet, but a second suspension is a live tail-risk).
  Three things about fal that cost real debugging time, so don't rediscover them:
  **(1)** auth is `Authorization: Key <k>`, not `Bearer`. **(2)** the queue returns its own
  `status_url`/`response_url` and you **must use them verbatim** — for a nested endpoint fal
  collapses the trailing path segment (`fal-ai/flux/dev` polls at `fal-ai/flux/requests/<id>`),
  so a hand-built path 404s on exactly the models we want. The mock in `tests/test_fal.py`
  reproduces that trap deliberately. **(3)** fal returns **no token usage**, so `usage` stays
  empty — real spend has to be read off fal's dashboard. Never let it be estimated.
  `return_last_frame` / `camera_fixed` / `seed` don't exist on fal and are **rejected at submit**
  rather than dropped; `chain()` refuses outright on a fal-only pool.
- **fal pricing (posted per-second, output/text-to-video, verified 2026-07-27):** 2.0 720p
  $0.3034/s · 2.0 1080p $0.682/s · 2.0-fast 720p $0.2419/s · **mini 480p $0.0721/s** · mini 720p
  $0.1547/s. Mini at 480p is the workhorse that makes a $10 trial into a real test matrix.
  Confirms the flat 2× vs BytePlus for the flagship.
- **Pushed and live** (verified 2026-07-27): repo `joshuamorris10451/seedance-batch` PUBLIC at
  HEAD `800ce0c`, brief serving 200 with `noindex,nofollow` + inline-SVG favicon, tests 18/18
  green. Collaborator invites to SilentAurora245 + mary3862jon are **sent but not yet accepted**
  (GitHub personal-repo invites expire after 7 days → re-send if they lapse). No release built —
  none needed, it's a source repo, not a ZIP handoff.
- Self-hosting is the other route for real volume: on Bob's planned RTX PRO 6000 + 5090 rig,
  LTX-2.3 / Wan 2.2 run at roughly $0.01/clip marginal, ~500–1,000 clips/day. Rig is still a
  plan, not built. Open-weight quality sits ~250 Elo behind Seedance 2.0 — fine for volume
  social, not for hero shots.
