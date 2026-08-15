# Shelfie

Turns a photo of a bookshelf into a structured personal library. Built for
the MealVue Full Stack Developer (AI & Computer Vision) t
## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React Native + Expo (TypeScript) | One codebase, runs via Expo Go, no native build needed. |
| Backend | Django + Django REST Framework | All heavy work lives server-side; device power is irrelevant. |
| Local detection | YOLOv8n (`ultralytics`, CPU) | Free, ~5-10x faster than Faster R-CNN on CPU, has a built-in `book` class. Locates spines; doesn't read them. |
| Vision model | Google Gemini (`gemini-3.5-flash-lite`) | Reads title/author off cropped spines in one batched call per shelf. |
| Database | SQLite | Single file, no server, graders can run it from a clean clone. |

## Pipeline

1. Photo taken/picked in the Expo app, force-converted to JPEG on-device.
2. Uploaded to `POST /api/scans/`.
3. YOLOv8n finds and crops each book spine **locally** (CPU, no cost).
4. Tall-narrow crops are rotated 90° (spine text runs vertically), then
   **batched into a single Gemini call** that returns title + author for
   every crop at once.
5. Each read is scored against `catalog/catalog.csv` — see
   [Matching](#matching) below.
6. High-confidence matches auto-add to the library. Everything else routes
   to the review screen.
7. Confirmed books persist to the library, viewable as a list.

The whole pipeline runs **synchronously** inside the request — see
[Honest caveats](#honest-caveats).

## Matching

The differentiating idea: **confidence is separation, not just similarity.**
`backend/scanner/services/matcher.py` takes the top candidate's blended
score *and* its margin over the second-best candidate. Auto-accept requires
both a high score and a clear margin; otherwise the read routes to review.
All thresholds live as named constants in
[`backend/scanner/constants.py`](backend/scanner/constants.py).

- **Normalize** both sides: lowercase, strip accents, drop punctuation and a
  leading article.
- **Title score:** fuzzy match (`rapidfuzz`) against the canonical title
  *and every alternate title*.
- **Author score:** structured, not fuzzy — parse "Lastname, Firstname" and
  glued-initial forms, compare surnames, then check initial compatibility.
- **Blend:** 75% title / 25% author, capped when the author couldn't be read
  at all (so a title-only match can never auto-accept).

This one rule handles every ambiguity the brief calls for — see
[`catalog/AMBIGUITIES.md`](backend/catalog/AMBIGUITIES.md) for exactly where
each of the six is planted and how the matcher resolves it:

| Ambiguity | Resolution |
|---|---|
| Two editions, same book | Tie → needs review, user picks |
| UK/US title (Philosopher's/Sorcerer's Stone) | Alt-title hit → matches cleanly, no review |
| Two different books, same title (*The Power*) | Author breaks the tie if read; otherwise → review |
| Omnibus + individual volumes | Exact volume title outscores the omnibus |
| Substring titles (*Dune* / *Dune Messiah*) | Correct title ranks first, but margin is too small to auto-accept → review |
| Author name in multiple forms | Normalizes to the same surname+initials regardless of form |

## Human in the loop

Every read lands in one of three buckets — **auto-add**, **needs review**,
**unmatched** — never silently accepted, never silently dropped. The review
screen shows the actual cropped spine photo next to the top candidates, with
three actions: confirm, correct (catalog search or free text), or discard.
Only confirmed detections reach the library.

## Graceful failure

| Failure | Handling |
|---|---|
| VLM timeout / API error | 45s timeout, one retry with backoff, then `status="failed"` with a retry action in the review screen. |
| Malformed JSON | Strip code fences, slice first `[` to last `]`, parse; on failure, one repair retry telling the model its output was invalid; then routed to review as `unreadable`. |
| Zero books detected | Falls back to one full-image Gemini call. If still nothing, friendly empty state with photo tips. |
| Unreadable spine | Review card with the crop and a free-text field — never dropped. |
| HEIC / odd formats | Converted on-device (`expo-image-manipulator`) and again server-side (`pillow-heif`) as a safety net. |
| Client-side network errors | Alert with a retry action; no blank screens. |

Every one of these paths has a dedicated (mocked) pytest case in
[`backend/scanner/tests/test_pipeline_failure_modes.py`](backend/scanner/tests/test_pipeline_failure_modes.py).

## Running it

### Backend

```bash
cd backend
python3.11 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py load_catalog
./venv/bin/python manage.py runserver 0.0.0.0:8000
```

Add a `.env` file in `backend/` with `GEMINI_API_KEY=<your key>` (a working
key is required for real scans; the pytest suite mocks the VLM entirely and
doesn't need one).

### Tests

```bash
cd backend
./venv/bin/python -m pytest scanner/tests/ -v
```

43 tests, no network calls, no live API key required.

### Mobile

```bash
cd mobile
npm install
npm start
```

The iOS Simulator and web (`npm run web`) reach `127.0.0.1:8000` directly. A
physical device needs the Mac's LAN IP in `mobile/.env`
(`EXPO_PUBLIC_API_URL=http://<your-lan-ip>:8000`).

## Measured performance

Run against two real iPhone photos (HEIC, 4284×5712) of an actual desk/shelf,
not staged or synthetic images. Full request timings, straight from
`Scan.timings`:

| Stage | Photo 1 (1 spine detected) | Photo 2 (7 boxes detected) |
|---|---|---|
| HEIC → JPEG convert | 248ms | 271ms |
| YOLOv8n detect (CPU) | 865ms | 195ms |
| Crop + rotate | 12ms | 46ms |
| Batched Gemini read | 5,234ms | 12,201ms |
| Match against catalog | 10ms | 31ms |
| **Total (upload → results)** | **6.4s** | **12.7s** |

Cost, from Gemini's own reported token usage on these calls (`gemini-2.5-flash`,
$0.30/1M input tokens, $2.50/1M output tokens incl. thinking tokens, per
[Google's published pricing](https://ai.google.dev/gemini-api/docs/pricing)):

| | Photo 1 (1 crop) | Photo 2 (7 crops, batched) |
|---|---|---|
| Input tokens | 297 | 1,974 |
| Output tokens (incl. thinking) | 557 | 898 |
| **Cost** | **~$0.0015** | **~$0.0028** |

**The batching payoff, measured, not assumed:** calling Gemini once per crop
instead of once per shelf would cost roughly 7 × $0.0015 ≈ $0.0104 for photo
2's seven crops — batching them into one call cut that to $0.0028, a **~73%
reduction**, on top of avoiding six extra round-trips of latency.

**What actually happened, read honestly:**
- Photo 1 shows two books held together (*The 48 Laws of Power*, *The Art of
  War*); YOLO only detected one of the two spines — a real miss, not a
  hypothetical one, likely because the second spine was thin and pressed
  flush against the first.
- Photo 2 shows three books; YOLO returned 7 boxes — two genuine spines were
  each detected twice (overlapping boxes that should have been suppressed),
  and two boxes were false positives on background clutter (a cardboard box
  and the blurry edge of a book stack). Both false positives were correctly
  flagged `unreadable` by Gemini rather than fed a fabricated title.
- Every spine Gemini *did* read text from, it read correctly — "THE 48 LAWS
  OF POWER" / "ROBERT GREENE", "AND THEN THERE WERE NONE" / "Agatha
  Christie", "THIS IS GOING TO HURT" / "ADAM KAY", "THE THIRTEEN PROBLEMS" /
  "AGATHA CHRISTIE" all read verbatim off the spine.
- None of these five real books are in the 109-entry catalog, so every read
  correctly landed in `needs_review` rather than being wrongly auto-added —
  the confidence+margin rule held up against real photos, not just the
  planted catalog ambiguities.

**A finding worth being honest about:** re-running the exact same crop (the
"48 Laws of Power" spine) through the production read function three times
at default settings returned three *different* results — one correct, two
confident hallucinations of real-but-wrong titles ("Swann's Way", "The
Rover"). Setting `temperature=0` made repeat calls consistent, but not
necessarily correct — one deterministic configuration reliably returned yet
a different wrong answer. This means the review workflow isn't a nice-to-
have on top of a reliable reader; it's load-bearing against real,
measured VLM inconsistency on a single call. It's also the practical
argument for the batched-call design being paired with per-detection
`status="failed"`/`retry` rather than trusting one read outright.

## What was cut

Per CONTEXT.md's cut order, nothing has been cut yet — bounding-box overlay,
crop thumbnails, catalog typeahead, and library delete/edit are all
implemented. If something needed to go, it would go in that order:
bounding-box overlay → crop thumbnails → catalog typeahead → library
delete/edit. Matcher quality, the review flow, failure handling, and this
README's numbers were never on the table.

## Honest caveats

- **Synchronous pipeline.** A real product with thousands of users would
  move detection/VLM/matching onto a background queue (Celery + polling)
  instead of blocking the request. SQLite is right for a single-clone
  grading exercise, not for concurrent production traffic.
- **Thresholds are reasoned defaults**, validated against the six planted
  catalog ambiguities via unit tests — not tuned against a large labeled set
  of real shelf photos, because one wasn't available. They're named
  constants specifically so this is a one-line change.

## Defense notes

- **Why YOLOv8n over Faster R-CNN?** 5-10x faster on CPU, which is where
  this has to run given the "device power is irrelevant" design goal —
  detection is local and free, only the VLM call costs money.
- **Why batch the VLM call?** A whole shelf becomes one or two Gemini calls
  instead of one per spine — the difference between ~20 calls and 1-2 is the
  headline cost number for this project.
- **Why fuzzy rules + margin instead of embeddings?** At ~100 catalog
  entries, rule-based matching is transparent, fast, and unit-testable.
  Embeddings would add infrastructure (a vector store, a second model) for
  no real accuracy gain at this scale.
- **Why synchronous instead of Celery?** Simplicity for a scoped exercise
  graded on a clean clone — see Honest caveats above for what a real
  deployment would do differently.
