"""Read title and author off spine crops with a hosted vision model.

Every crop from one photo goes in **one** request. The prompt is the expensive
part of a per-spine call and it is identical for all of them, so batching
sends it once instead of once per book, and collapses N round trips into one.

Hardened against the ways a model returns something other than what it was
asked for: fences and prose are sliced away, and a body that still will not
parse earns exactly one repair retry. Past that the scan fails with a code the
UI can act on -- never a crash, and never a silently dropped spine.

What it does not do, on purpose: correct, complete, or canonicalize anything.
It transcribes. Deciding that "GRIECHISCHES RECHTSDENKEN" is a particular
catalog row is the matcher's job, and mixing the two would hide read errors
inside match errors.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence

from ..constants import (
    GEMINI_MODEL,
    VLM_MAX_CROPS_PER_CALL,
    VLM_TEMPERATURE,
    VLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

PROMPT = """You are transcribing book spines cropped from a photo of a bookshelf.

You will receive {count} images, numbered 0 to {last} in the order given.

For each image, report the title and author printed on that spine.

Return ONLY a JSON array, one object per image, in index order:
[{{"index": 0, "title": "...", "author": "...", "duplicate_of": null}}]

Rules:
- "index" must be the image's position in the order received.
- Return exactly {count} objects, one per image, even if some are unreadable.
- Crops can overlap, so the same physical book sometimes appears twice. When
  an image shows the same copy of the same book as an EARLIER image, set
  "duplicate_of" to that earlier index. Otherwise set it to null. Two
  different copies of the same title are NOT duplicates.
- Transcribe what is printed. Do not correct spelling, expand abbreviations,
  translate, or complete a title you recognise but cannot fully read.
- Use an empty string for anything you cannot read. Never guess.
- The author is often in smaller type at the top or bottom of the spine. If
  only a surname is printed, report only the surname.
- Ignore publisher names, series numbers, and library stickers."""

REPAIR_PROMPT = """Your previous response was not valid JSON and could not be parsed.

Reply again with ONLY the JSON array. No explanation, no markdown fences, no
text before or after it. Exactly {count} objects, indices 0 to {last}."""


class ReadErrorCode(str, Enum):
    """Why a read failed, in terms the UI can act on.

    The distinction that matters to a user is not which exception fired, it is
    whether trying again in a moment will help. RATE_LIMITED and UNAVAILABLE
    are worth a retry button; NOT_CONFIGURED and AUTH are not, and telling
    someone to "try again" on a bad API key just wastes their time.
    """

    NOT_CONFIGURED = 'not_configured'
    AUTH = 'auth'
    RATE_LIMITED = 'rate_limited'
    TIMEOUT = 'timeout'
    UNAVAILABLE = 'unavailable'
    MALFORMED_RESPONSE = 'malformed_response'
    UNKNOWN = 'unknown'

    @property
    def is_retryable(self) -> bool:
        return self in {
            ReadErrorCode.RATE_LIMITED,
            ReadErrorCode.TIMEOUT,
            ReadErrorCode.UNAVAILABLE,
        }


# What the user is shown. Deliberately free of status codes and provider
# names: "429 RESOURCE_EXHAUSTED" is not a sentence anyone can act on.
READ_ERROR_MESSAGES = {
    ReadErrorCode.NOT_CONFIGURED: (
        'Spine reading is not configured on the server.'
    ),
    ReadErrorCode.AUTH: (
        'The server was refused access to the reading service.'
    ),
    ReadErrorCode.RATE_LIMITED: (
        'Too many scans in a short time. Wait about a minute and try again.'
    ),
    ReadErrorCode.TIMEOUT: (
        'Reading the spines took too long. Try again, or use a photo of a '
        'single shelf.'
    ),
    ReadErrorCode.UNAVAILABLE: (
        'The reading service is temporarily unavailable. Try again shortly.'
    ),
    ReadErrorCode.MALFORMED_RESPONSE: (
        'The reading service returned something unreadable. Try again.'
    ),
    ReadErrorCode.UNKNOWN: 'Could not read the spines in this photo.',
}


class VlmReadError(RuntimeError):
    """The read stage failed. No partial results survive it.

    Carries both a `code` for the client to branch on and a `detail` holding
    the raw provider text, which stays in the log and out of the UI.
    """

    def __init__(self, code: ReadErrorCode, detail: str = ''):
        self.code = code
        self.detail = detail
        super().__init__(READ_ERROR_MESSAGES[code])

    @property
    def user_message(self) -> str:
        return READ_ERROR_MESSAGES[self.code]

    @property
    def is_retryable(self) -> bool:
        return self.code.is_retryable


def classify(exc: Exception) -> ReadErrorCode:
    """Map a provider exception onto a code the UI understands.

    Keyed on HTTP status where the SDK exposes one, because that is the only
    part of a provider error that is stable. Message text is not.
    """
    status = getattr(exc, 'code', None)
    if isinstance(status, int):
        if status == 429:
            return ReadErrorCode.RATE_LIMITED
        if status in (401, 403):
            return ReadErrorCode.AUTH
        if status >= 500:
            return ReadErrorCode.UNAVAILABLE
        if status == 400:
            # A malformed request is almost always a bad or absent key here,
            # since the request body itself is built by this module.
            return ReadErrorCode.AUTH

    name = type(exc).__name__.lower()
    if 'timeout' in name or 'deadline' in name:
        return ReadErrorCode.TIMEOUT
    if 'connect' in name:
        return ReadErrorCode.UNAVAILABLE

    return ReadErrorCode.UNKNOWN


@dataclass(frozen=True)
class SpineRead:
    """One transcribed spine, tied back to the crop it came from by index."""

    index: int
    title: str
    author: str
    #: Index of an earlier crop showing this same physical book, when the
    #: detector boxed one spine twice. Always resolved to a root and always
    #: less than `index`, so the chain cannot loop.
    duplicate_of: int | None = None

    @property
    def is_empty(self) -> bool:
        return not self.title.strip()

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


def extract_json_array(text: str) -> str:
    """Pull a JSON array out of whatever the model wrapped it in.

    Two failure modes, both common enough to be worth handling before spending
    a second call on a retry:

    1. Markdown fences. The model is asked for JSON and returns ```json ...```
       despite response_mime_type, particularly when it also wants to explain
       itself.
    2. Prose on either side. "Here are the spines: [...]  Let me know if..."

    Slicing first `[` to last `]` handles both, and is safe because the schema
    is a top-level array -- there is nothing legitimate outside it to lose.
    """
    cleaned = text.strip()

    if '```' in cleaned:
        # Take the largest fenced block rather than the first: a model that
        # explains itself in a small fence and answers in a big one is more
        # common than the reverse.
        blocks = re.findall(r'```(?:json|JSON)?\s*(.*?)```', cleaned, re.DOTALL)
        if blocks:
            cleaned = max(blocks, key=len).strip()

    start = cleaned.find('[')
    end = cleaned.rfind(']')
    if start != -1 and end > start:
        return cleaned[start : end + 1]

    return cleaned


def resolve_duplicates(pointers: dict[int, int], count: int) -> dict[int, int]:
    """Validate and flatten model-supplied duplicate pointers.

    The model is asked to say when two crops show the same physical book. It
    is not trusted to say it coherently, so every pointer is checked:

    - **Self-reference.** `5 -> 5` is dropped. It is meaningless and, followed
      naively, is an infinite loop.
    - **Out of range.** An index outside 0..count-1 is dropped rather than
      raising: one bad pointer should not fail a whole shelf.
    - **Forward references.** Only pointing at an *earlier* crop is allowed.
      This is what makes cycles structurally impossible rather than merely
      unlikely, so `2 -> 3, 3 -> 2` cannot deadlock the resolver.
    - **Chains.** `3 -> 2 -> 1` collapses to `3 -> 1`, so every duplicate
      names the original rather than another duplicate.
    """
    valid: dict[int, int] = {}
    for index, target in pointers.items():
        if not 0 <= index < count or not 0 <= target < count:
            continue
        # Backward-only. Also rejects self-reference, since index < index
        # is never true.
        if target >= index:
            continue
        valid[index] = target

    resolved: dict[int, int] = {}
    for index in sorted(valid):
        target = valid[index]
        # Targets are strictly smaller and already resolved, so this is a
        # single lookup, not a walk.
        resolved[index] = resolved.get(target, target)
    return resolved


def get_client():
    """Build a Gemini client from the environment.

    Imported lazily and constructed per call rather than cached at module
    level: the key is read from the environment, and a module-level client
    would capture whatever was set at import time.
    """
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key:
        raise VlmReadError(
            ReadErrorCode.NOT_CONFIGURED,
            'GEMINI_API_KEY is not set. See backend/.env.example.',
        )

    from google import genai

    return genai.Client(api_key=api_key)


def _parse(text: str, expected: int) -> list[SpineRead]:
    """Parse the model's JSON array into reads, keyed by index.

    No repair. A malformed body raises, which is the point of this phase --
    the failure has to be visible before it is worth handling.

    Indexing is defensive in one direction only: the model is told to return
    every index and is trusted to say what it read, but not trusted to return
    them in order or to return all of them. Missing entries become empty reads
    rather than silently shifting every later spine onto the wrong book.
    """
    try:
        payload = json.loads(extract_json_array(text))
    except json.JSONDecodeError as cause:
        raise VlmReadError(
            ReadErrorCode.MALFORMED_RESPONSE, f'Not JSON: {cause}'
        ) from cause

    if not isinstance(payload, list):
        raise VlmReadError(
            ReadErrorCode.MALFORMED_RESPONSE,
            f'Expected a JSON array, got {type(payload).__name__}.',
        )

    by_index: dict[int, SpineRead] = {}
    pointers: dict[int, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get('index'))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < expected:
            continue
        by_index[index] = SpineRead(
            index=index,
            title=str(item.get('title') or '').strip(),
            author=str(item.get('author') or '').strip(),
        )
        raw_pointer = item.get('duplicate_of')
        if raw_pointer is not None:
            try:
                pointers[index] = int(raw_pointer)
            except (TypeError, ValueError):
                # A non-numeric pointer is simply not a duplicate claim.
                pass

    # Validated separately so one incoherent pointer cannot corrupt the rest.
    for index, target in resolve_duplicates(pointers, expected).items():
        by_index[index] = replace(by_index[index], duplicate_of=target)

    if len(by_index) != expected:
        logger.warning(
            'Model returned %d of %d expected reads; filling the gaps as unread.',
            len(by_index),
            expected,
        )

    # Position is the only link back to the crop, so every index must exist.
    return [
        by_index.get(index, SpineRead(index=index, title='', author=''))
        for index in range(expected)
    ]


def read_spines(crops: Sequence[bytes]) -> list[SpineRead]:
    """Transcribe every crop in a single call. Returns one read per crop."""
    if not crops:
        return []

    if len(crops) > VLM_MAX_CROPS_PER_CALL:
        raise VlmReadError(
            ReadErrorCode.UNKNOWN,
            f'{len(crops)} crops exceeds the {VLM_MAX_CROPS_PER_CALL} per-call '
            f'limit. Splitting across calls is a later phase.',
        )

    from google.genai import types

    client = get_client()
    prompt = PROMPT.format(count=len(crops), last=len(crops) - 1)

    contents = [prompt]
    contents.extend(
        types.Part.from_bytes(data=crop, mime_type='image/jpeg') for crop in crops
    )

    config = types.GenerateContentConfig(
        temperature=VLM_TEMPERATURE,
        response_mime_type='application/json',
        http_options=types.HttpOptions(timeout=VLM_TIMEOUT_SECONDS * 1000),
    )

    logger.info('Reading %d crop(s) in one %s call', len(crops), GEMINI_MODEL)

    def call(parts):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL, contents=parts, config=config
            )
        except VlmReadError:
            raise
        except Exception as cause:
            code = classify(cause)
            # Full provider text goes to the log, never to the user.
            logger.warning('Gemini call failed (%s): %s', code.value, cause)
            raise VlmReadError(code, str(cause)) from cause

    response = call(contents)
    text = response.text or ''

    try:
        return _parse(text, len(crops))
    except VlmReadError as first:
        if first.code is not ReadErrorCode.MALFORMED_RESPONSE:
            raise

        # Exactly one repair attempt. The images are re-sent because the API
        # is stateless, so this costs a second full read -- which is why it
        # happens once and is not a loop. A model that cannot produce an array
        # twice will not produce one on the third try either.
        logger.warning('Unparseable read, attempting one repair: %s', first.detail)
        repair = REPAIR_PROMPT.format(count=len(crops), last=len(crops) - 1)
        retry_contents = [prompt, *contents[1:], text[:2000], repair]

        retry = call(retry_contents)
        try:
            reads = _parse(retry.text or '', len(crops))
        except VlmReadError as second:
            logger.warning('Repair retry also unparseable: %s', second.detail)
            raise
        logger.info('Repair retry succeeded')
        return reads
