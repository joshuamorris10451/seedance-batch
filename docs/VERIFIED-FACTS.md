# Seedance — primary-source verified facts (2026-07-27)

Everything here was pulled by me directly from ByteDance/BytePlus primary sources, not from
SEO blogspam. Anything NOT verified is marked. Treat `seedance2.so`, `easemate.ai`,
`gamsgo`, `apiframe`, `evolink`, `lumiying`, `novoads`, `soravideo.art` etc. as affiliate
SEO spam — several of them contradict the official docs.

## 1. Version reality

| Version | Status (2026-07-27) | Notes |
|---|---|---|
| Seedance 1.0 pro / pro-fast / lite | Live, international | `seedance-1-0-pro-250528`, `seedance-1-0-pro-fast-251015` |
| Seedance 1.5 pro | Live, international | `seedance-1-5-pro-251215` |
| **Seedance 2.0** | **LIVE internationally on BytePlus ModelArk** | 3 variants, see below |
| Seedance 2.5 | Announced 2026-06-23, China enterprise beta | NOT on BytePlus international model list |

- Seedance 2.0 officially launched **2026-02-12** (seed.bytedance.com). 15s multi-shot
  audio+video, accepts up to 9 images + 3 video clips + 3 audio clips + text.
- Global rollout was **voluntarily paused ~March 2026** after cease-and-desist letters from
  Disney, Warner Bros. Discovery, Paramount Skydance, Netflix and Sony Pictures over
  copyrighted characters. ByteDance added C2PA provenance watermarks, face-blocking filters
  and copyrighted-character detection.
- It then **came back**: BytePlus ModelArk opened the Seedance 2.0 API. The model IDs below
  are present in BytePlus's own live model list and pricing docs today.
- **Seedance 2.5** was announced by Volcano Engine president Tan Dai at the FORCE conference
  on **2026-06-23**: native 30s, up to 50 multimodal reference inputs, native 4K. China-first
  (Jimeng/即梦 experience centre 2026-07-06, Doubao, CapCut, Volcengine API). It is **not yet
  on the BytePlus international model list** — I checked the live list and it is absent.
  seed.bytedance.com also does not mention it.

**=> The working version for us today is Seedance 2.0 on BytePlus ModelArk.**

## 2. The live international API (verified by direct call)

- Base URL: `https://ark.ap-southeast.bytepluses.com/api/v3`
- Endpoint: `POST /contents/generations/tasks` (async) then poll `GET /contents/generations/tasks/{id}`
- Auth: `Authorization: Bearer $ARK_API_KEY`
- API key console: `https://console.byteplus.com/ark/region:ark+ap-southeast-1/apikey`
- Python SDK: `pip install 'byteplus-python-sdk-v2[ark]'` → `from byteplussdkarkruntime import Ark`
- Also official Go and Java SDKs.

Reachability test from this box (2026-07-27):
```
POST .../contents/generations/tasks  ->  HTTP 401
{"error":{"code":"AuthenticationError","message":"The API key format is incorrect."...}}
console.byteplus.com  ->  HTTP 200
```
So the endpoint is live and reachable; only a key is missing.

### Request shape (from BytePlus's own sample code)
```python
client.content_generation.tasks.create(
    model="dreamina-seedance-2-0-260128",
    content=[
        {"type": "text",      "text": "..."},
        {"type": "image_url", "image_url": {"url": "..."}, "role": "reference_image"},
        {"type": "video_url", "video_url": {"url": "..."}, "role": "reference_video"},
        # audio_url / role "reference_audio" also supported
    ],
    generate_audio=True,
    ratio="16:9",       # 9:16 for our reels
    duration=11,        # seconds
    watermark=True,     # can be set False
)
```
Poll returns `status` in `succeeded` / `failed` / (running), and `video_url` on success.

### Full parameter set (read out of the installed official SDK, not from docs)

`byteplussdkarkruntime.resources.content_generation.tasks.create()` accepts:

```
model, content,
safety_identifier, callback_url, return_last_frame, service_tier,
execution_expires_after, priority, generate_audio, draft, camera_fixed,
watermark, seed, resolution, ratio, duration, frames
```

Four of these matter a lot for a batch tool and are not obvious from the marketing pages:
- **`callback_url`** — webhook on completion, so we don't have to poll thousands of tasks.
- **`return_last_frame`** — returns the final frame, which can be fed straight back in as the
  first frame of the next clip. That is native **clip chaining**: build a 60s sequence out of
  15s generations with continuity.
- **`camera_fixed`** — locked camera. Exactly what the VegToons look-lock rule requires.
- **`seed`** — reproducible generations, so a good result can be re-rolled deterministically.

Also `draft` (cheap preview pass) and `priority` / `service_tier` (queue control).

SDK verified working on this box: `pip install 'byteplus-python-sdk-v2[ark]' sniffio anyio httpx`
then `from byteplussdkarkruntime import Ark`. (`sniffio` is a missing transitive dep — install
it explicitly.)

## 3. Model matrix (from the live BytePlus model list)

| Model ID | Resolutions | Duration | FPS | Individual limits | Enterprise limits |
|---|---|---|---|---|---|
| `dreamina-seedance-2-0-260128` | 480p, 720p, 1080p, **4K (10-bit)** | 4–15 s | 24 | 180 RPM / **3 concurrent** | 600 RPM / 10 concurrent |
| `dreamina-seedance-2-0-fast-260128` | 480p, 720p | 4–15 s | 24 | 180 RPM / **3 concurrent** | 600 RPM / 10 concurrent |
| `dreamina-seedance-2-0-mini-260615` | 480p, 720p | 4–15 s | 24 | 180 RPM / **3 concurrent** | 600 RPM / 10 concurrent |
| `seedance-1-5-pro-251215` | 480p, 720p, 1080p | 4–12 s | 24 | — | 600 RPM / 10 concurrent |

4K on the full model is separately throttled: **15 RPM / 1 concurrent** for everyone.

All three 2.0 variants support: audio-visual sync, text-to-video, image-to-video (first
frame), image-to-video (first *and last* frames), multimodal reference-to-video, video
modification, and video extension. Output is .mp4.

Note `flex: Not supported` on the 2.0 variants (no discounted off-peak tier), whereas
1.5-pro has a flex tier with TPD 500B.

**Important correction to the blogspam:** several sites claim "1080p not supported" for
Seedance 2.0 generally. Wrong — that limitation applies only to the **fast** and **mini**
variants. The full `dreamina-seedance-2-0-260128` does 1080p and 4K.

**There IS an "individual users" tier** — you do not need to be an enterprise to call this.

## 4. Region availability

BytePlus ModelArk lists availability in **190+ countries and territories** (US, Canada, EU,
Australia, Japan, most of APAC/Africa/LatAm), with a carve-out for unspecified "Restricted
Models". Docs are English; support in English + Chinese. Availability is "determined at the
point of purchase", and no KYC detail is published.

## 4b. Timeline of the suspension and the quiet reopening

- **2026-02-12** Seedance 2.0 launches in China.
- **2026-02-13** Disney sends a cease-and-desist alleging a "pirated library" of its characters.
  Paramount, Warner Bros., Netflix, the MPA and SAG-AFTRA follow within days.
- **2026-02-24** the planned global API launch date passes; postponed.
- **2026-03-14/15** ByteDance officially **suspends the overseas API release**. China-side
  access (Jimeng, Doubao, Volcengine Ark) never went down.
- **2026-03-16** US senators demand ByteDance shut Seedance down.
- **2026-03-26** CapCut ships "Dreamina Seedance 2.0" internationally (ID/PH/TH/VN/MY/BR/MX),
  with no real-face generation, IP blocking, visible + invisible watermarks and C2PA.
- **2026-04-07** CapCut expands to US/Japan/Europe/Africa/South America/Middle East.
- **~2026-05-29** the 2.0 model IDs appear in BytePlus ModelArk docs — the API reopens quietly,
  with no formal "un-suspension" announcement.
- **2026-06-15** Seedance 2.0 Mini ships.
- **2026-06-23** Seedance 2.5 announced at Volcano Engine FORCE (30s, 50 refs, native 4K).

**No lawsuit has actually been filed** — only C&Ds, an MPA denunciation and a Senate letter.
The underlying copyright dispute is unresolved. The model now blocks prompts naming
celebrities, trademarked logos and specific artistic styles.

## 4c. Quality ranking (Artificial Analysis, pulled 2026-07-27)

Text-to-video: #1 Gemini Omni Flash 1,247 · **#2 Dreamina Seedance 2.0 720p 1,229** ·
#3 Wan 2.7 (API) 1,165 · #6 Kling 3.0 1080p Pro 1,113 · #11 Veo 3.1 1,096.
Image-to-video: #1 Gemini Omni Flash 1,200 · **#2 Dreamina Seedance 2.0 720p 1,199** ·
#7 Veo 3.1 1,088 · #11 Kling 3.0 Pro 1,075.

So Seedance 2.0 is genuinely near the top — and clearly **ahead of Veo 3.1, which is what
veo-flow-multi currently drives**. Seedance 2.5 has no leaderboard entry; any Elo quoted for
it today is fabricated.

## 4d. Conflict to be aware of

A research pass claimed Seedance **2.5** got a public BytePlus API on 2026-07-16 (sourced to
TechTimes, which 403s on fetch, plus SEO sites). **My own direct pull of the live BytePlus
model list does not contain any 2.5 model ID.** Primary source wins: assume 2.5 is *not*
callable internationally yet, and design for 2.0 with a config-driven model ID so 2.5 is a
one-line change when it appears.

## 5. Pricing — VERIFIED

Independently verified against the BytePlus rate card by a separate agent, and cross-checked
against my own derivation. **The verification corrected me**: I had derived $0.69 for a 5s 720p
clip from the China CNY rate; the actual international figure printed by BytePlus is **$0.76**.

**Billing formula** (BytePlus's own, confirmed to reproduce their printed token counts exactly):

```
tokens = (input_video_duration + output_duration) × width × height × 24 ÷ 1024
```

Billed per million tokens, **only on success**. Actual consumption comes back in
`usage.completion_tokens` — always read it rather than estimating.

**Rates (USD per 1M tokens, online inference):**

| Model | No video input | With video input |
|---|---|---|
| `dreamina-seedance-2-0-260128` 480p/720p | 7.0 | 4.3 |
| `dreamina-seedance-2-0-260128` 1080p | 7.7 | 4.7 |
| `dreamina-seedance-2-0-260128` 4K | 4.0 | 2.4 |
| `dreamina-seedance-2-0-fast-260128` | 5.6 | 3.3 |
| `dreamina-seedance-2-0-mini-260615` | 3.5 | 2.1 |
| `seedance-1-5-pro-251215` | 2.4 with audio / 1.2 silent | — |
| `seedance-1-0-pro-250528` | 2.5 | — |
| `seedance-1-0-pro-fast-251015` | 1.0 | — |

Note the 4K per-token rate is *lower*, but 4K burns ~4× the tokens of 1080p, so a 4K clip still
costs about 2.1× a 1080p one.

**Cost per clip, 16:9, no video input** (P = printed by BytePlus, D = derived):

| Model | 720p 5s | 720p 10s | 1080p 5s | 1080p 10s |
|---|---|---|---|---|
| 2.0 | **$0.76** P | $1.51 D | **$1.87** P | $3.74 D |
| 2.0-fast | **$0.60** P | $1.21 D | n/a | n/a |
| 2.0-mini | **$0.38** P | $0.76 D | n/a | n/a |
| 1.5-pro (audio) | $0.26 P | $0.52 D | $0.58 P | $1.17 D |
| 1.5-pro (silent) | $0.13 P | $0.26 D | $0.29 P | $0.58 D |
| 1.0-pro | $0.26 P | $0.51 P | $0.61 P | $1.22 P |
| 1.0-pro-fast | $0.10 P | $0.21 P | $0.24 P | $0.49 P |

**Going direct is cheapest for the 2.0 family.** fal.ai charges exactly 2× the official token
rate ($0.014/1K vs $0.007/1K), WaveSpeed ~60% over, Kie.ai ~35% over. For the older 1.x family
that inverts — Kie undercuts official by 30–45% (1.0-pro-fast 720p 5s at $0.08).

**Free tier — RESOLVED 2026-07-27** (was previously marked unverified; Bob pushed back that a
trial covers 5–10 videos, and he was right). Primary sources, extracted from the JS-rendered Lark
docs:

- `docs.byteplus.com/en/docs/ModelArk/1399514` (Inference free trial): *"Free inference quota is
  calculated **separately for different models** and shared under the primary account."* Worked
  example in the doc uses 500 (k tokens) per model. Sub-accounts do **not** multiply it.
- `docs.byteplus.com/en/docs/ModelArk/1465347` (Free Tokens Only mode): *"calls to the inference
  API consume only the **500k free tokens** granted by the platform"*, available to *"identity-
  verified personal accounts and enterprise accounts"*. One-way switch — once disabled it cannot
  be re-enabled.
- `docs.byteplus.com/en/docs/ModelArk/1928265`: *"If multiple accounts are associated with the same
  mobile phone number, the same identification document, the same account ID … they will be
  regarded as the same user."* Plus explicit reservation of the right to disqualify *"resource
  hoarding, bulk abuse"*.

So the marketed "2m tokens" is **500k × several models**, not 2m on one. Per verified account, at
5s/720p (108,000 tokens per clip — 4 whole clips per model):

| Model | free clips | value |
|---|---|---|
| `dreamina-seedance-2-0-260128` | 4 | $3.04 |
| `dreamina-seedance-2-0-fast-260128` | 4 | $2.40 |
| `dreamina-seedance-2-0-mini-260615` | 4 | $1.52 |
| `seedance-1-5-pro-251215` | 4 | $1.04 |
| `seedance-1-0-pro-250528` | 4 | $1.04 |
| `seedance-1-0-pro-fast-251015` | 4 | $0.40 |
| **total** | **24 clips** | **$9.44, once** |

At 480p (~48,038 tok/clip) it's ~10 clips per model ≈ **60 clips per account**. Bob's "5–10
videos" was accurate and conservative.

**Reseller signup credits are the easier free door** and are what rival batch tools actually
advertise: fal.ai gives new accounts **$10 with no credit card** (≈6 Seedance 720p clips at their
2× rate), WaveSpeed and Replicate give smaller no-card grants, Higgsfield runs periodic
unlimited-24h promos. Email only, **no identity check** — unlike BytePlus. Those sellers are not
farming; the free clips are their own paid credit used as a lead magnet for a subscription.

⚠ **Corrected reasoning:** an earlier version of the brief argued farming risked Bob's client
Google Ads via multi-account ban cascade. That was imported from the Veo/Flow context and is
**wrong here** — BytePlus is ByteDance and cannot touch a Google account. The real blockers are
identity verification and the same-identity clause above.

Still unverified: minimum-token floors when input includes video (Lark wiki behind login), and
the 480p pixel basis for the 2.0 family ($0.35/5s back-solves to 50,000 tokens, which matches no
standard 480p geometry — the price is printed, the derivation isn't).
