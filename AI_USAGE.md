# AI_USAGE.md

## Summary

I used AI assistance throughout this project, primarily Claude (chat) for
architecture and design discussion, and Claude Code for implementation
scaffolding and debugging. My honest estimate is that **65 to 75 percent of the
committed code was AI-generated or AI-assisted**, with the remainder written or
substantially rewritten by hand.

What was *not* delegated was the design. Every architectural decision, the
matching algorithm, the confidence model, and the catalog construction were
mine. AI accelerated the typing and caught my mistakes faster than I would have
caught them alone; it did not decide what to build.

---

## Where AI was used

### Environment and setup
- Confirming the Python version and dependency set, and checking library
  compatibility (ultralytics, Pillow, rapidfuzz, DRF versions).
- Generating the initial Django project and app scaffold, and the Expo project
  scaffold. Standard boilerplate, reviewed and trimmed.
- Resolving the local networking configuration for physical-device testing
  (LAN IP vs localhost vs emulator loopback, ALLOWED_HOSTS).

### Code generation
- Django models, serializers, and view boilerplate from a schema I specified.
- React Native screen scaffolding and navigation wiring.
- The first draft of the YOLO inference wrapper and the crop/rotate utilities.
- Test scaffolding for the matcher, from cases I enumerated.

### Debugging
- Reading tracebacks and locating the source of errors, particularly around
  image format handling (HEIC decode failures) and multipart upload wiring.
- Diagnosing malformed VLM responses and hardening the JSON parsing path.

### Documentation
- Drafting and editing this file and the README, from my notes and measurements.

---

## Where the decisions were mine

Before writing any code I worked out the pipeline on paper: where the image
originates, what carries it to the server, where it is decoded and normalised,
what runs detection, what the crops are handed to, and how the returned text is
reconciled against a catalog. That flow, and the failure branches hanging off
each stage, came first. The code followed it.

The following were my calls, made with AI as a sounding board rather than a
source:

**Local model: YOLOv8n on CPU.** Chosen as a gate and localiser so the hosted
model receives focused spine crops rather than a whole shelf. I considered
torchvision Faster R-CNN (permissively licensed but five to ten times slower on
CPU) and Grounding DINO (too slow on CPU for this budget), and rejected both on
measured latency grounds.

**Hosted model: Gemini Flash tier.** Selected primarily on image token
economics. Gemini encodes an image far more cheaply than the alternatives I
priced, which matters when a single shelf produces fifteen crops. It also
performs strongly on short-text OCR, which is exactly the task here.

**Batching the VLM call.** All crops for a scan go up in one request with
enforced structured output, rather than one call per book. This is the single
biggest driver of the cost-per-image figure in the README, and it was a
deliberate design choice, not an optimisation applied afterwards.

**Rotating tall-narrow crops before inference.** Spine text runs vertically;
rotating materially improves read quality. Found by inspecting my own failure
cases on real photos.

**The matching algorithm.** This is the core of the project and it is my work.
Normalisation strategy (diacritic folding, article stripping, subtitle
splitting), the decision to score titles fuzzily but authors structurally, the
weighting between them, and the confidence cap when an author is unreadable
were all specified by me before implementation.

**Confidence as separation, not similarity.** The idea that a match should be
auto-accepted only when the top score is both high *and* clearly ahead of the
runner-up is the key insight in this codebase. A raw similarity score cannot
distinguish "confidently correct" from "confidently confused between two near
identical entries." Using the margin to the second-best candidate resolves every
ambiguity class I planted in the catalog, with one rule. This was mine.

**Catalog construction.** I decided which ambiguity classes the catalog needed
to contain in order for the matcher to be meaningfully tested: two editions of
one work, regional retitles, distinct books sharing a title, omnibus editions
overlapping their contained volumes, titles that are substrings of other titles,
and author name variants. I chose the specific entries and I know where each
trap lives. I also weighted the catalog toward books an engineering team is
plausibly likely to own, so the demo exercises real matches rather than misses.

**Not using LangChain.** The entire model interaction is one structured call.
Adding an orchestration framework would have introduced a dependency tree and an
abstraction layer over a single function call, with no chains, agents, memory,
or retrieval to justify it. I use LangChain in other projects where retrieval
and multi-step reasoning warrant it; it does not belong here.

**Human-in-the-loop design.** The review screen shows the actual cropped spine
beside the top candidates, so the user is verifying against the evidence rather
than trusting a label. Nothing enters the library unconfirmed, and nothing is
silently discarded. That behaviour was specified before the UI existed.

**Synchronous request handling.** A deliberate scope decision for this exercise,
with the production alternative (task queue plus polling) documented in the
README rather than half-implemented.

---

## Verification

I reviewed every diff before committing. Where AI-generated code was accepted
unchanged, it was because I read it and agreed with it, not because I did not
look. Specifically:

- The matcher was reviewed line by line and partially rewritten; its tests were
  written against cases I derived from the catalog, not generated from the
  implementation.
- All latency and cost figures in the README were measured on my own machine
  with my own test images. None are estimated, and none came from a model.
- I can explain and justify any line in this repository.

---

## What I would flag honestly

AI assistance made this faster, not different. The eight-hour scope was the
binding constraint, and the time saved on boilerplate went into the matching
logic and the failure paths, which is where I judged the value to be. Had I
written every line by hand, the architecture would have been identical and the
matcher would have been thinner.
