"""Read title and author off spine crops with a hosted vision model.

Every crop from one photo goes in **one** request. The prompt is the expensive
part of a per-spine call and it is identical for all of them, so batching
sends it once instead of once per book, and collapses N round trips into one.

This is deliberately the naive version. One call, no retry, no JSON repair. It
fails loudly so that the phases which harden it have something real to fix.

What it does not do, on purpose: correct, complete, or canonicalize anything.
It transcribes. Deciding that "GRIECHISCHES RECHTSDENKEN" is a particular
catalog row is the matcher's job, and mixing the two would hide read errors
inside match errors.
"""

import json
import logging
import os
from dataclasses import dataclass
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
[{{"index": 0, "title": "...", "author": "..."}}]

Rules:
- "index" must be the image's position in the order received.
- Return exactly {count} objects, one per image, even if some are unreadable.
- Transcribe what is printed. Do not correct spelling, expand abbreviations,
  translate, or complete a title you recognise but cannot fully read.
- Use an empty string for anything you cannot read. Never guess.
- The author is often in smaller type at the top or bottom of the spine. If
  only a surname is printed, report only the surname.
- Ignore publisher names, series numbers, and library stickers."""


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

    @property
    def is_empty(self) -> bool:
        return not self.title.strip()


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
        payload = json.loads(text)
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

    logger.info('Reading %d crop(s) in one %s call', len(crops), GEMINI_MODEL)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=VLM_TEMPERATURE,
                # Asking for JSON is configuration, not repair. There is still
                # no fallback if the body comes back malformed.
                response_mime_type='application/json',
                http_options=types.HttpOptions(timeout=VLM_TIMEOUT_SECONDS * 1000),
            ),
        )
    except VlmReadError:
        raise
    except Exception as cause:
        code = classify(cause)
        # Full provider text goes to the log, never to the user.
        logger.warning('Gemini call failed (%s): %s', code.value, cause)
        raise VlmReadError(code, str(cause)) from cause

    if not response.text:
        raise VlmReadError(
            ReadErrorCode.MALFORMED_RESPONSE, 'Empty response body.'
        )

    return _parse(response.text, len(crops))
