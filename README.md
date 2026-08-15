# Shelfie

Photograph a bookshelf, get a structured personal library. A local detector
finds each spine, one batched vision-model call reads them all, and a
confidence-and-margin matcher decides what's certain enough to keep and what
a human should look at.

```
photo → convert → detect spines → crop & rotate → batched VLM read → match → library
```

---

## Quick start

Everything runs through [mise](https://mise.jdx.dev), which manages the
toolchain and every task. Install it first:

**macOS**
```bash
brew install mise
# or, without Homebrew:
curl https://mise.run | sh
```

**Windows**
```powershell
winget install jdx.mise
# or:  scoop install mise
# or:  choco install mise
```

**Linux**
```bash
curl https://mise.run | sh
```

Then add it to your shell (`~/.zshrc`, `~/.bashrc`, or PowerShell profile) —
`mise --help` prints the exact line for your shell:

```bash
eval "$(mise activate zsh)"      # bash / fish / pwsh also supported
```

You also need **Python 3.11+** and, on Windows, **Git Bash** (ships with
[Git for Windows](https://gitforwindows.org)) — the tasks and `scripts/` are
POSIX shell, and mise is configured to route them through bash there. Node is
installed by mise itself.

One command then installs the Python virtualenv, the npm packages, the
database and the catalog:

```bash
mise trust               # once, per clone
mise run setup
```

Add your Gemini key to `backend/.env`:

```
GEMINI_API_KEY=your-key-here
```

Then confirm the environment is sane:

```bash
mise run doctor
```

```
Toolchain
  ✓ node        v24.19.0
  ✓ npm         11.17.0
  ✓ python      3.11.15 (backend/venv)
Backend
  ✓ python deps installed
  ✓ GEMINI_API_KEY present
  ✓ migrations up to date
  ✓ catalog loaded (114 books)
  ✓ YOLO weights present (yolov8x.pt)
Mobile
  ✓ node_modules installed
  ✓ Expo SDK    ~54.0.0
  ✓ React Native 0.81.5
  ✓ API URL     http://192.168.2.175:8000
```

Then run it. Either the whole project in one terminal:

```bash
mise run dev          # backend + Expo together, output prefixed, Ctrl-C stops both
```

```
[backend] System check identified no issues (0 silenced).
[mobile]  Starting Metro Bundler
[mobile]  Waiting on http://localhost:8081
[backend] [15/Aug/2026 21:16:04] "GET /api/library/ HTTP/1.1" 200 3105
```

…or each half in its own terminal, which is what you want when you're
actually reading Django's request log:

```bash
mise run backend      # Django on :8000  — request log, tracebacks, SQL
mise run mobile       # Expo on :8081    — QR code, Metro bundling
```

---

## Every command

| Command | What it does |
|---|---|
| `mise run setup` | Install everything: venv, pip, npm, migrations, catalog |
| `mise run doctor` | Check the environment; prints the fix for anything missing |
| `mise run dev` | **Backend + Expo together**, prefixed output, one Ctrl-C stops both |
| `mise run backend` | Django API on `:8000` (migrates first, auto-reloads) |
| `mise run mobile` | Expo on `:8081` — QR for Expo Go, press `w` for browser |
| `mise run web` | Expo in the browser only |
| `mise run lan` | Point the app at this Mac's LAN IP (needed for a physical phone) |
| `mise run sdk [version]` | Switch Expo SDK; no argument lists what's published |
| `mise run catalog` | Reload `catalog.csv` — see [Known issues](#known-issues) |
| `mise run test` | 110 pytest tests, VLM mocked — no network, no cost |
| `mise run typecheck` | `tsc --noEmit` over the mobile app |
| `mise run check` | Everything that runs offline: tests + typecheck |
| `mise run pipeline` | Run a real photo through every stage — **billed** |

Extra arguments pass straight through:

```bash
mise run test -k matcher
mise run pipeline --skip-vlm      # free: skips the billed VLM node
```

---

## Running on a real phone

Expo Go on the App Store only ever supports **one** SDK — the current one. If
your Expo Go is older, the project must come down to meet it, because the
phone cannot come up.

```bash
mise run sdk            # what's installed, and every published SDK
mise run sdk 54         # switch the whole project to SDK 54
```

That pins `expo` first and then runs `expo install --fix`, so React, React
Native and every `expo-*` package land on versions that SDK actually ships.
Order matters — running `--fix` first would align everything to the *old* SDK.

Then point the app at your machine, because a phone cannot reach the Mac's
`127.0.0.1` — that address resolves to the phone itself, and every request
fails with a bare "Network error":

```bash
mise run lan            # writes your current LAN IP into mobile/.env
mise run mobile         # scan the QR with Expo Go
```

Re-run `mise run lan` whenever you change network. `EXPO_PUBLIC_*` values are
inlined at **bundle** time, so restart Metro after it changes — reloading the
app is not enough.

**No Expo Go?** Open `http://<your-lan-ip>:8081` in the phone's browser. The
camera still works: the web image picker sets the `capture` attribute, so
"Take Photo" opens the real camera rather than the photo library.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React Native + Expo (TypeScript) | One codebase, runs in Expo Go, no native build. |
| Backend | Django + DRF | All heavy work server-side; device power is irrelevant. |
| Detection | YOLOv8x (`ultralytics`, CPU) | Free, local, has a `book` class. Locates spines; never reads them. |
| Reading | Gemini `gemini-3.5-flash-lite` | Reads every cropped spine in one batched call per shelf. |
| Matching | `rapidfuzz` + structured author parsing | Transparent and unit-testable at ~100 entries. |
| Database | SQLite | Single file, no server, runs from a clean clone. |

The reader is provider-pluggable via `VLM_PROVIDER` — Gemini and Claude share
the prompt, JSON parsing, retry semantics and failure statuses; only the
transport differs.

---

## How the pipeline works

Each stage is independently checkable with `mise run pipeline`:

```
node 0  catalog    PASS  114 books loaded                             1 ms
node 1  convert    PASS  2298KB heic -> 4696KB jpeg 4284x5712       256 ms
node 2  detect     PASS  3 spine boxes                             1231 ms
node 3  crop       PASS  3 crops, 3 rotated upright                  28 ms
node 4  vlm read   PASS  3/3 readable, 0 duplicate                 2066 ms
node 5  match      PASS  3 scored -- 3 auto, 0 review, 0 unmatched   13 ms
```

**1 · Convert.** Any upload is force-decoded and re-encoded as JPEG
(`pillow-heif` handles iPhone HEIC). The normalised JPEG is what gets stored,
so `image_url` is always something a browser can render.

**2 · Detect.** YOLO returns raw boxes, which over-count badly — it draws
several boxes down one spine and calls cardboard cartons "book". Three passes
clean that up in `services/yolo_detect.py`:

- **Shape** — a shelved spine is tall and narrow (`h/w ≥ 2.5`). Drops cartons,
  shelf edges, background clutter. Falls back to the unfiltered set if it
  would empty the frame, so a photo of books lying flat still works.
- **Slivers** — anything thinner than 1.5% of the image width is a fragment.
- **Merge** — boxes that are the same spine seen twice, absorbed into their
  union so a fragment grows back to the whole spine.

Merging is the dangerous direction, so it needs two independent signals to
agree. High IoU merges. High *containment* only merges when the two boxes are
of **comparable width** — a narrow box inside a wide one is usually a real
neighbouring spine the wide box swallowed, and dropping it loses a book. On
top of that, a near-full-height vertical edge through the overlap (Canny,
peak-vs-median) means two physical spines touching, and blocks the merge
outright.

**3 · Crop & rotate.** Padded crops, rotated 90° when taller than they are
wide, because spine text runs vertically. No downscaling — that's what blurs
small type.

**4 · Read.** Every crop goes into **one** batched Gemini call returning
title, author, spine colour, readability and duplicate links for all of them.
Colour is constrained to a closed vocabulary, because a model asked to name a
colour freely says "mustard" where the catalog says "yellow".

**5 · Match.** See below.

The whole thing runs **synchronously** inside the request — see
[Honest caveats](#honest-caveats).

---

## Catalog construction

`catalog/catalog.csv` holds **114 entries**, designed as an adversarial test
fixture rather than a booklist.

The governing question was: *what would let a matcher appear correct while being
wrong?* Fuzzy matching succeeds trivially when every entry sits far from every
other, so a catalog of unrelated titles validates nothing. Each ambiguity class
below was planted deliberately, and the matcher is required to survive all of
them.

| Planted | Instances | What it breaks if you get it wrong |
|---|---|---|
| Two editions of one work | `Fahrenheit 451` ×2 (1953, 2013) | Identical title *and* author — only the year separates them |
| Regional retitles | 31 rows carry `alt_titles` | *Northern Lights* / *The Golden Compass*; Philosopher's / Sorcerer's Stone |
| Different books, same title | `The Power` ×2, `The Secret` ×2 | Alderman vs Byrne; Byrne vs Garwood |
| Omnibus vs its volumes | 2 omnibus rows + their volumes | LOTR and Hitchhiker's, each alongside the individual books |
| Substring titles | *Dune* / *Dune Messiah*; the Foundation trilogy | A `contains` match conflates them; fuzzy nearly does |
| Author name variants | Same author stored 3 ways | `Orwell, George` vs `George Orwell`; `García` vs `Garcia`; `J.K.` vs `J. K.` |

Author names are stored **inconsistently by design** — the same author appears in
several forms across rows, so matching cannot assume a normalised catalog. Real
catalogs never are.

The remaining entries span 1813–2021 and are weighted toward titles an
engineering team plausibly owns, so a demo exercises genuine matches rather than
misses alone. 37 carry a `series`, 113 a `year`.

Five entries — *The Art of War*, *The 48 Laws of Power*, *This Is Going to Hurt*,
*And Then There Were None*, *The Thirteen Problems* — were added after
photographing my own shelf, giving the committed test images full end-to-end
coverage through detection, match, review, and library.

**Data integrity: `spine_color` is populated only for those five books.** The
other 109 are empty because the matcher reads that field, and inventing a colour
for a book nobody here has seen would feed fabricated data into a scoring path.
Empty means unknown, and the tie-break skips it — an explicit rule, applied
consistently.

Every trap is documented with its expected resolution in
[`AMBIGUITIES.md`](backend/catalog/AMBIGUITIES.md), and each is pinned by a test
— so "the matcher handles this" is verified by the suite, not asserted here.

---

## Matching

The differentiating idea: **confidence is separation, not just similarity.**
`services/matcher.py` takes the top candidate's blended score *and* its margin
over the runner-up. Auto-accept needs both.

- **Normalize** both sides: lowercase, strip accents and punctuation, drop a
  leading article.
- **Title score:** fuzzy match against the canonical title *and every*
  alternate title.
- **Author score:** structured, not fuzzy — parse `Lastname, Firstname` and
  glued initials, compare surnames, then check initial compatibility.
- **Blend:** 75% title / 25% author, capped when the author was unreadable, so
  a title-only match can never auto-accept.
- **Colour tie-break:** when the text scores leave a near-tie, the candidate
  whose spine colour matches the photo is shown first. Deliberately
  presentation-only — it never changes status, confidence or margin. Colour is
  a weak signal under unknown lighting, and a wrong auto-add reaches the
  library with no human step in the way.

Six ambiguities are planted in the catalog on purpose. See
[`catalog/AMBIGUITIES.md`](backend/catalog/AMBIGUITIES.md):

| Ambiguity | Resolution |
|---|---|
| Two editions, same book | Tie → review, user picks the edition |
| UK/US title (Philosopher's/Sorcerer's Stone) | Alt-title hit → clean match, no review |
| Two different books, same title (*The Power*) | Author breaks the tie; otherwise review |
| Omnibus + individual volumes | Exact volume title outscores the omnibus |
| Substring titles (*Dune* / *Dune Messiah*) | Right title ranks first, margin too small to auto-accept → review |
| Author name in multiple forms | Normalizes to the same surname + initials |

---

## Changing behaviour

Every threshold is a named constant in
[`backend/scanner/constants.py`](backend/scanner/constants.py) — tuning is a
one-line edit, and `mise run test` tells you immediately if you broke a
planted ambiguity.

| Want to… | Change |
|---|---|
| Auto-accept more freely | `AUTO_ACCEPT_SCORE_THRESHOLD`, `AUTO_ACCEPT_MARGIN_THRESHOLD` |
| Show fewer hopeless candidates | `REVIEW_MIN_SCORE_THRESHOLD` |
| Trust the author more | `TITLE_WEIGHT` / `AUTHOR_WEIGHT` |
| Detect more (or fewer) spines | `YOLO_CONFIDENCE_THRESHOLD`, `MIN_SPINE_ASPECT_RATIO` |
| Merge duplicate boxes harder | `DEDUP_IOU_THRESHOLD`, `DEDUP_CONTAINMENT_THRESHOLD`, `DEDUP_MIN_WIDTH_RATIO` |
| Swap the vision model | `GEMINI_MODEL` / `CLAUDE_MODEL`, or `VLM_PROVIDER` in `.env` |
| Add colours to the catalog | `spine_color` / `spine_hex` columns in `catalog.csv`, then `mise run catalog` |

Adding books is a CSV edit plus `mise run catalog`. Columns: `title`,
`author`, `alt_titles` (pipe-separated), `year`, `series`, `is_omnibus`,
`contained_titles`, `spine_color`, `spine_hex`. The colour columns are
optional — blank means unknown, and a malformed hex is dropped rather than
rendered as a wrong swatch.

---

## Human in the loop

Every read lands in one of **auto-add**, **needs review**, **unmatched**,
**unreadable** — never silently accepted, never silently dropped. The review
screen shows the actual cropped spine next to ranked candidates, with three
actions: confirm, correct (catalog search or free text), or discard. Only
confirmed detections reach the library.

## Graceful failure

| Failure | Handling |
|---|---|
| VLM timeout / API error | 45s timeout, one retry with backoff, then `status="failed"` with a retry action. |
| Malformed JSON | Strip fences, slice first `[` to last `]`; on failure one repair retry; then `unreadable` → review. |
| Zero books detected | Falls back to one full-image call. Still nothing → friendly empty state. |
| Unreadable spine | Review card with the crop and a free-text field — never dropped. |
| HEIC / odd formats | Converted server-side (`pillow-heif`), and the stored image is the JPEG. |
| Duplicate boxes on one spine | Geometry merge, plus the VLM's own `duplicate_of` → collapsed, not re-asked. |
| Client-side network errors | Visible alert with retry (`react-native-web`'s `Alert` is a no-op, so there's a shim). |

Each has a dedicated mocked test in
[`test_pipeline_failure_modes.py`](backend/scanner/tests/test_pipeline_failure_modes.py).

---

## Tests

```bash
mise run check      # 110 pytest tests + tsc, all offline
```

| File | Covers |
|---|---|
| `test_matcher.py` | Normalization, author parsing, the six planted ambiguities |
| `test_spine_dedup.py` | Box geometry, the merge decision, the "never lose a book" case |
| `test_spine_color.py` | Colour vocabulary, prompt contents, tie-break invariants |
| `test_catalog_loader.py` | CSV loading, optional colour columns, malformed hex |
| `test_vlm_parsing.py` | JSON extraction, repair, normalization |
| `test_pipeline_failure_modes.py` | Every row of the failure table above |

No network, no API key, no cost.

---

## Measured performance

Six consecutive scans of real iPhone photos (HEIC, 4284×5712), straight from
`Scan.timings`:

| Stage | Median |
|---|---|
| HEIC → JPEG convert | 190 ms |
| YOLO detect (CPU) | 500 ms |
| Crop + rotate | 18 ms |
| Batched Gemini read | 1,970 ms |
| Match | 17 ms |
| **Total (upload → results)** | **2.6 s** |

Accuracy on every scan run through the complete pipeline:

```
19 detections    auto_added  19  (100.0%)
```

All five books read verbatim and matched correctly, including a five-book
shelf in a single photo.

**A finding worth being honest about:** re-running the *same* crop through the
reader three times once returned three different answers — one correct, two
confident hallucinations of real-but-wrong titles. `temperature=0` made repeats
consistent but not necessarily correct. The review workflow isn't decoration on
a reliable reader; it's load-bearing against measured VLM inconsistency.

---

## Cost

Token counts below are read straight off Gemini's `usage_metadata` for the two
test photos, through the production crop path:

| Photo | Crops | Input | ├ text | └ image | Output | Total |
|---|---|---|---|---|---|---|
| shelf_1 | 2 | 2,584 | 424 | 2,160 | 138 | 2,722 |
| shelf_2 | 3 | 3,680 | 428 | 3,252 | 204 | 3,884 |

### What actually drives the bill

**Images are ~85% of input, and output is negligible.** A crop costs about
**1,082 input tokens** regardless of what's printed on it. The prompt is a flat
~426 tokens per call, and the JSON that comes back is only 138–204 tokens. So
the bill is essentially *crop count × 1,082*, and every lever that matters is a
lever on how many crops you send.

### Cost per scan by model

For a typical 3-crop shelf (3,680 in / 204 out), at Google's published
developer rates:

| Model | $/1M in | $/1M out | Per scan | Per 1,000 scans |
|---|---|---|---|---|
| Gemini 2.5 Flash-Lite | 0.10 | 0.40 | $0.00045 | **$0.45** |
| **Gemini 3.5 Flash-Lite** ← current | 0.30 | 2.50 | $0.00161 | **$1.61** |
| Gemini 3.6 / 3.7 Flash | 0.75 | 3.75 | $0.00352 | **$3.52** |
| Gemini 3.1 Pro (≤200K) | 2.00 | 12.00 | $0.00981 | **$9.81** |
| Gemini 3.5 Flash | 2.70 | 16.20 | $0.01324 | **$13.24** |

Two things fall out of that table that are easy to get wrong:

**Gemini 3.5 Flash costs more than 3.1 Pro for this workload** — $13.24 vs
$9.81 per thousand scans. Flash is not automatically the cheap option; its
output rate ($16.20/1M) is above Pro's ($12.00/1M), and "Flash" only describes
latency. Read the rate card, not the tier name.

**Dropping to 2.5 Flash-Lite is a 3.6× saving** and worth testing, precisely
because this workload is short-answer perception rather than reasoning. Spine
reading may not need the newer model at all — that's a measurable experiment,
not a guess, and `mise run pipeline` is how you'd run it.

### The two levers, measured

**Fewer crops — by far the bigger one.** Raw YOLO returned **9** boxes on the
three-book shelf (a cardboard carton, blurry background clutter). The
shape/sliver/merge passes bring it to **3**:

| | Crops | Input tokens | Output | Cost/scan | Per 1,000 |
|---|---|---|---|---|---|
| Raw YOLO | 9 | ~10,164 | 475 | $0.00424 | $4.24 |
| After filtering | 3 | 3,680 | 204 | $0.00161 | **$1.61** |

**A 62% cut**, and it *improves* accuracy at the same time — readable rate went
3/9 → 3/3, because the discarded boxes were cardboard being paid for at full
image-token rate.

**Batching — real, but smaller than it looks.** One call per shelf instead of
one per spine saves the repeated prompt, not the images:

| Crops | Batched (1 call) | Unbatched (N calls) | Saving |
|---|---|---|---|
| 3 | 3,672 | 4,524 | 19% |
| 7 | 8,000 | 10,556 | 24% |

Only the ~426-token prompt is deduplicated; image tokens are incompressible.
An earlier draft of this README claimed ~73% by comparing one shelf's total
against another's — wrong methodology, corrected here. The **latency** win is
the larger one anyway: six fewer round-trips on a seven-crop shelf, and network
time dominates the ~2s read stage.

### At scale

At the current model and 3 crops per scan:

| Volume | Cost |
|---|---|
| 100 scans/day | **$4.84/month** |
| 1,000 scans/day | **$48/month** |
| 10,000 scans/day | **$484/month** |

Google's asynchronous **Batch API** halves both rates, taking 100/day to
~$2.42/month — but it resolves within 24 hours, so it can't back an interactive
scan. It would suit a re-processing backfill, not the live path.

> Rates are as published for the developer tier and were supplied for this
> analysis rather than verified against Google's pricing page at build time.
> The token counts are measured and won't drift; re-check the dollar rates
> before quoting them, and note the published increases scheduled for
> 1 Jan 2027.

---

## Known issues

**Reloading the catalog blanks existing library rows.** `load_catalog` deletes
and recreates every `CatalogBook`, and `LibraryEntry.book` is
`on_delete=SET_NULL`, so entries lose their title and render blank. The fix is
to snapshot title and author onto `LibraryEntry` at confirm time — a library is
a record of what someone owns, not a live pointer into a reference table.

**`fuzz.WRatio` inflates short-read-vs-long-title scores.** A read that isn't in
the catalog can score 0.855 against something unrelated (*And Then There Were
None* → a TensorFlow textbook) and reach the review card as a plausible
candidate instead of landing in `unmatched`. A token-prefix rule falling back to
`fuzz.ratio` fixes it while preserving every planted ambiguity.

Also unfinished, deliberately: provenance badges and a detection detail view in
the library, and an evaluation set larger than two shelves.

---

## With another day

**The two bugs above, first.** Both are scoped rather than open-ended — the
matcher fix is roughly fifteen lines with a design already validated against all
six planted ambiguities, and the library fix is two fields plus a migration.
Neither is research; they were a time decision, not an unsolved problem.

**A dynamic catalog.** Today it is a static CSV loaded wholesale, and
`load_catalog` deletes every row before recreating it. I would make loading an
incremental upsert — which fixes the library-blanking bug as a side effect — and
then let an unmatched read reach outward to Open Library or Google Books and
become a catalog entry rather than a dead end. That is the honest answer to the
sharpest limitation here: a fixed 114-entry catalog cannot contain *your* books,
so the interesting path for any real user is the one that ends in `unmatched`.

**Gemma 4 running locally, replacing the hosted call.** This removes the
per-scan cost entirely and deletes the network round-trip, which is currently
~2s of a ~2.6s scan. The reader is already provider-pluggable behind
`VLM_PROVIDER`, so it is a new transport module rather than a rewrite. The
tradeoff needs measuring rather than assuming: local CPU inference may well be
slower than the API call it replaces, and read quality has to be benchmarked
against the current baseline before "free" counts as "better."

**UI depth.** Provenance badges distinguishing auto-matched from
corrected-in-review from custom entry, and a detail view putting the original
spine crop beside the matched catalog record and the confidence that produced
it. Both make the confidence model visible rather than merely implemented.

**Live detection in the camera preview — deliberately not yet.** Running the
detector per-frame is the obvious next feature and I am choosing to skip it,
because it would surface YOLO's instability rather than fix it. Boxes flicker
frame to frame; a spine detected at frame 10 vanishes at frame 11 and returns at
frame 12. Running detection once on a still hides that, and the dedup passes
clean up what remains. Putting it in a live preview would make the product feel
unreliable while producing exactly the same final result. It needs temporal
smoothing across frames — track boxes, require a detection to persist before
drawing it — and that is a real piece of work, not a UI toggle. Shipping the
flicker would be shipping a worse-feeling product for a more impressive-sounding
feature.

---

## Honest caveats

- **Synchronous pipeline.** Real traffic would move detect/read/match onto a
  queue (Celery + polling) instead of blocking the request. SQLite is right for
  a single-clone exercise, not concurrent production.
- **Thresholds are reasoned defaults** validated against the planted
  ambiguities and two real photos — not tuned against a large labelled set,
  because one wasn't available. They're named constants precisely so that's a
  one-line change.
- **Small evaluation set.** 19/19 across five distinct books on two shelves in
  decent light says the pipeline works on *those* shelves. More photos —
  denser, dimmer, worn paperbacks — are the honest next step before claiming
  general accuracy.
- **Spine colour is lighting-dependent.** The same book reads `red` in one
  photo and `orange` in another. That's exactly why colour only re-ranks and
  never decides.
- **Verified on macOS only.** The tasks and `scripts/` are POSIX shell and
  handle both virtualenv layouts (`venv/bin` and Windows' `venv/Scripts`), and
  `mise run lan` detects an address on macOS, Linux and Git Bash. But the
  Windows and Linux paths are written, not tested — I had no machine to run
  them on. If `mise run lan` can't find your address it says so and tells you
  what to put in `mobile/.env` by hand.

## Defense notes

- **Why YOLO over Faster R-CNN?** 5-10× faster on CPU, and detection has to be
  local and free — only the VLM call costs money.
- **Why batch the VLM call?** A whole shelf becomes one call instead of ~20.
  That's the headline cost number.
- **Why fuzzy rules instead of embeddings?** At ~100 entries, rule-based
  matching is transparent, fast and unit-testable. Image embeddings would also
  need *spine* reference images — cover APIs serve front covers, which look
  nothing like a spine — so the retrieval side has a data problem before it has
  an accuracy benefit.
- **Why is colour advisory only?** It's a weak signal under unknown lighting,
  demonstrably drifting between photos of the same book. Re-ranking a review
  card is free; a wrong auto-add is not.
