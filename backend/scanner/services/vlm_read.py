"""
Batched Gemini vision call: reads title/author off cropped book-spine
images in a single request. Implements the documented failure modes from
CONTEXT.md's "Graceful failure" table:

- VLM timeout / API error -> hard timeout, one retry with backoff, then the
  caller gets a `status="failed"` placeholder (never a crash).
- Malformed JSON -> strip code fences, slice first `[` to last `]`, parse;
  on failure, one repair retry telling the model its output was invalid;
  then the caller gets `status="unreadable"`, routed to review.
"""

import base64
import json
import logging
import re
import time

import anthropic
from django.conf import settings
from google import genai
from google.genai import types

from scanner.constants import (
    CLAUDE_EFFORT,
    CLAUDE_MAX_TOKENS,
    CLAUDE_MODEL,
    GEMINI_MODEL,
    SPINE_COLOR_SYNONYMS,
    SPINE_COLOR_VOCABULARY,
    VLM_MAX_RETRIES,
    VLM_RETRY_BACKOFF_SECONDS,
    VLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)
_REPAIR_NOTE = (
    "Your previous response was not valid JSON. Respond again with ONLY the "
    "JSON array in the exact schema requested -- no markdown code fences, "
    "no commentary, just the array."
)

_client = None
_claude_client = None


class VLMCallError(Exception):
    """The Gemini call itself failed (timeout, network, API error)."""


class VLMReadError(Exception):
    """The Gemini call succeeded but its output couldn't be parsed as JSON."""


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise VLMCallError("GEMINI_API_KEY is not configured")
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _get_claude_client() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise VLMCallError("ANTHROPIC_API_KEY is not configured")
        # max_retries=0: the SDK retries by default, which would silently
        # multiply the one deliberate retry in _call_with_network_retry.
        _claude_client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=VLM_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _claude_client


def extract_json_array(text: str) -> str:
    """Strip ``` code fences and slice from the first '[' to the last ']'."""
    text = _CODE_FENCE_RE.sub("", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise VLMReadError(f"no JSON array found in model output: {text[:200]!r}")
    return text[start : end + 1]


def parse_read_response(text: str) -> list[dict]:
    """Parses model output into a list of read-result dicts, repairing common wrapping issues."""
    snippet = extract_json_array(text)
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError as exc:
        raise VLMReadError(f"invalid JSON after extraction: {exc}") from exc
    if not isinstance(data, list):
        raise VLMReadError(f"expected a JSON array, got {type(data).__name__}")
    return data


def normalize_spine_color(value) -> str | None:
    """
    Folds a model's colour word into the closed vocabulary, or returns None.

    The prompt asks for one of a fixed list, but a model will still hand back
    "dark navy blue" or "Grey". Anything that cannot be folded is dropped: a
    colour that matches no catalog value is worse than no colour, because it
    reads as evidence of a mismatch when it is really just vocabulary drift.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text in SPINE_COLOR_VOCABULARY:
        return text
    if text in SPINE_COLOR_SYNONYMS:
        return SPINE_COLOR_SYNONYMS[text]
    # "dark navy blue" -> blue. Last word first, since English puts the head
    # noun last and the modifiers ("dark", "pale") in front.
    for word in reversed(re.findall(r"[a-z]+", text)):
        if word in SPINE_COLOR_VOCABULARY:
            return word
        if word in SPINE_COLOR_SYNONYMS:
            return SPINE_COLOR_SYNONYMS[word]
    return None


def _build_batch_prompt(n: int) -> str:
    return (
        f"You are reading cropped photos of book spines from a bookshelf, "
        f"labeled Image 1 through Image {n}, in that exact order. For each "
        "image, read the title and author printed on the spine.\n\n"
        "Respond with ONLY a JSON array, one object per image in order, shaped "
        'like: {"index": <1-based image number>, "title": <string or null>, '
        '"author": <string or null>, "edition": <string or null> ,"readable": <true|false>, '
        '"spine_color": <string or null>, "not_a_book": <true|false>, '
        '"duplicate_of": <1-based image number or null>}\n\n'
        'Set "not_a_book" true only when the crop contains no book at all — a '
        "wall, a pillar, a cardboard box, furniture, a plant. A book whose "
        "spine you simply cannot read is NOT this: that is readable=false with "
        "not_a_book false. The distinction matters — an unreadable spine gets "
        "shown to a person to identify, while a pillar is thrown away.\n\n"
        f'For "spine_color", give the single dominant background colour of the '
        f"spine — not the colour of the lettering — using EXACTLY one of these "
        f"words: {', '.join(SPINE_COLOR_VOCABULARY)}. Pick the closest one "
        "rather than inventing a shade; use multicolour only when no single "
        "colour covers most of the spine, and null if the crop is too dark or "
        "washed out to judge. Report the colour even when the text is "
        "unreadable.\n\n"
        "Each image gets its own object — do not merge, skip, or repeat entries "
        "for any image, even if two spines look identical or belong to the same "
        "series.\n\n"
        "These crops come from an object detector that often draws several "
        "overlapping boxes around the SAME physical book. Still return an object "
        "for every image, but when two images show the same physical copy — same "
        "spine artwork, same text, same colours and fonts — keep the earliest and "
        'set "duplicate_of" on the later one to that earliest image number. Two '
        "separate copies, or two different editions of one title, are NOT "
        'duplicates: leave "duplicate_of" null unless it is literally the same '
        "physical object photographed twice.\n\n"
        "If a spine's text is too blurry, obscured, or cut off to confidently "
        "read, set readable to false and title/author to null. Do not include "
        "any text outside the JSON array."
    )


_FULL_IMAGE_PROMPT = (
    "This is a photo of a bookshelf or a stack of books; no individual "
    "spines were pre-cropped for you. Identify every book you can see and "
    "read its title and author directly from the photo.\n\n"
    'Respond with ONLY a JSON array of objects shaped like: {"index": '
    '<integer, 1-based order in which you identify the book>, "title": '
    '<string or null>, "author": <string or null>, "edition": <string or '
    'null>, "readable": <true|false>, "spine_color": <string or null>, '
    '"duplicate_of": <integer index or null>}.\n\n'
    f'For "spine_color", give the dominant background colour of that book\'s '
    f"spine — not the lettering — using EXACTLY one of these words: "
    f"{', '.join(SPINE_COLOR_VOCABULARY)}. Use null if you cannot judge it.\n\n"
    "If you cannot identify any books at all, respond with an empty JSON "
    "array: []."
)


def _build_image_parts(image_bytes_list: list[bytes]) -> list:
    parts: list = []
    for i, image_bytes in enumerate(image_bytes_list, start=1):
        parts.append(f"Image {i}:")
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
    return parts


def _generate_gemini(prompt: str, image_bytes_list: list[bytes]) -> str:
    client = _get_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[prompt, *_build_image_parts(image_bytes_list)],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=VLM_TIMEOUT_SECONDS * 1000),
        ),
    )
    print(f"Gemini response: {response}")
    return response.text or ""


def _generate_claude(prompt: str, image_bytes_list: list[bytes]) -> str:
    client = _get_claude_client()
    content: list[dict] = []
    for i, image_bytes in enumerate(image_bytes_list, start=1):
        content.append({"type": "text", "text": f"Image {i}:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                },
            }
        )
    # Instruction last, after the images it refers to.
    content.append({"type": "text", "text": prompt})

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        output_config={"effort": CLAUDE_EFFORT},
        messages=[{"role": "user", "content": content}],
    )
    # A safety decline is a successful HTTP 200 with empty/partial content --
    # check it before reading blocks, and route it down the same retry-then-
    # fail path as any other call failure.
    if message.stop_reason == "refusal":
        raise VLMCallError("Claude declined to read this image (stop_reason=refusal)")
    return "".join(block.text for block in message.content if block.type == "text")


def _generate(prompt: str, image_bytes_list: list[bytes]) -> str:
    # prompt += "avoid duplicate entries where authors and titles are the same."
    print("======================")
    print("prompt: ", prompt)
    print("======================")
    if settings.VLM_PROVIDER == "claude":
        return _generate_claude(prompt, image_bytes_list)
    return _generate_gemini(prompt, image_bytes_list)


def _call_with_network_retry(prompt: str, image_bytes_list: list[bytes]) -> str:
    # Broad except is intentional here: this is the one external-network
    # boundary in the pipeline, and every failure mode (timeout, DNS, 5xx,
    # malformed SDK response) must fall through to the same retry-then-fail
    # path rather than crashing the request.
    last_exc: Exception | None = None
    for attempt in range(VLM_MAX_RETRIES + 1):
        try:
            return _generate(prompt, image_bytes_list)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "%s call failed (attempt %d/%d): %s",
                settings.VLM_PROVIDER,
                attempt + 1,
                VLM_MAX_RETRIES + 1,
                exc,
            )
            if attempt < VLM_MAX_RETRIES:
                time.sleep(VLM_RETRY_BACKOFF_SECONDS)
    raise VLMCallError(str(last_exc))


def _call_with_json_repair(prompt: str, image_bytes_list: list[bytes]) -> str:
    raw_text = _call_with_network_retry(prompt, image_bytes_list)
    try:
        parse_read_response(raw_text)
    except VLMReadError:
        raw_text = _call_with_network_retry(f"{_REPAIR_NOTE}\n\n{prompt}", image_bytes_list)
    return raw_text


def _duplicate_target(item: dict, index: int, expected_count: int, already_duplicate: set[int]) -> int | None:
    """
    Validates a model-supplied "duplicate_of" pointer, returning the crop this
    one collapses into, or None. The model is not trusted to be consistent
    here: self-references, out-of-range indices and chains (3 -> 2 -> 1) are
    all rejected, because following them blindly can collapse every crop of a
    shelf and leave the user with an empty scan.
    """
    target = item.get("duplicate_of")
    # bool is an int subclass -- True would otherwise resolve to index 1.
    if isinstance(target, bool) or not isinstance(target, int):
        return None
    if not (1 <= target <= expected_count) or target == index:
        return None
    if target in already_duplicate:
        return None
    return target


def _normalize_batch_results(parsed: list, expected_count: int) -> list[dict]:
    by_index: dict[int, dict] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if isinstance(idx, int) and 1 <= idx <= expected_count:
            by_index[idx] = item

    results = []
    duplicates: set[int] = set()
    for i in range(1, expected_count + 1):
        item = by_index.get(i)
        readable = bool(item and item.get("readable", True) and item.get("title"))
        # The detector boxes pillars, cartons and furniture. Those are not
        # "a spine I couldn't read" -- nobody can identify them -- so they are
        # separated here and discarded rather than queued for review.
        not_a_book = bool(item and item.get("not_a_book"))
        # Only a crop we actually read can be a duplicate of another: calling an
        # unreadable crop a duplicate would hide it behind the wrong status.
        duplicate_of = _duplicate_target(item, i, expected_count, duplicates) if readable else None
        if duplicate_of is not None:
            duplicates.add(i)
        results.append(
            {
                "index": i,
                # Kept even for duplicates, so the collapsed row still shows
                # which book it was a second crop of.
                "title": item.get("title") if (item and readable) else None,
                "author": item.get("author") if item else None,
                # Kept even when the text was unreadable: colour is judged off
                # the crop, not the lettering, so a blurry spine can still
                # narrow the candidate list on the review card.
                "spine_color": normalize_spine_color(item.get("spine_color")) if item else None,
                "not_a_book": not_a_book,
                "readable": readable and duplicate_of is None,
                "status": (
                    "duplicate"
                    if duplicate_of is not None
                    # Checked before "unreadable": a pillar is technically
                    # unreadable too, but routing it to a person to identify
                    # buries the genuinely blurry spines that need them.
                    else "not_a_book"
                    if not_a_book and not readable
                    else "ok"
                    if readable
                    else "unreadable"
                ),
            }
        )
    return results


def read_spines_batch(image_bytes_list: list[bytes]) -> list[dict]:
    """
    One batched call reading title/author off every crop. Always returns a
    list the same length as the input -- unreadable/failed spines are
    represented, never dropped, so the pipeline can route them to review.
    """
    if not image_bytes_list:
        return []

    prompt = _build_batch_prompt(len(image_bytes_list))

    try:
        raw_text = _call_with_json_repair(prompt, image_bytes_list)
        parsed = parse_read_response(raw_text)
    except VLMCallError as exc:
        # Network/timeout/API error even after the retry -> the client gets
        # a "failed" placeholder with a retry button.
        logger.error("VLM batch read failed permanently (call error): %s", exc)
        return [
            {"index": i, "title": None, "author": None, "readable": False, "status": "failed"}
            for i in range(1, len(image_bytes_list) + 1)
        ]
    except VLMReadError as exc:
        # Still-malformed JSON even after the one repair retry -> route to
        # review as unreadable rather than surfacing a generic failure.
        logger.error("VLM batch read returned unparseable JSON after repair retry: %s", exc)
        return [
            {"index": i, "title": None, "author": None, "readable": False, "status": "unreadable"}
            for i in range(1, len(image_bytes_list) + 1)
        ]

    return _normalize_batch_results(parsed, len(image_bytes_list))


def read_full_image_fallback(image_bytes: bytes) -> list[dict]:
    """Used when YOLO finds zero book spines: one VLM call against the whole photo."""
    try:
        raw_text = _call_with_json_repair(_FULL_IMAGE_PROMPT, [image_bytes])
        parsed = parse_read_response(raw_text)
    except (VLMReadError, VLMCallError) as exc:
        logger.error("VLM full-image fallback failed permanently: %s", exc)
        return []

    return [item for item in parsed if isinstance(item, dict) and item.get("title")]
