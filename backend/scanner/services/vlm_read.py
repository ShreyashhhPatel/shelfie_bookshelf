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


class VlmReadError(RuntimeError):
    """The read stage failed. No partial results survive it."""


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
        raise VlmReadError('GEMINI_API_KEY is not set. See backend/.env.example.')

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
        raise VlmReadError(f'Model did not return JSON: {cause}') from cause

    if not isinstance(payload, list):
        raise VlmReadError(f'Expected a JSON array, got {type(payload).__name__}.')

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
            f'{len(crops)} crops exceeds the {VLM_MAX_CROPS_PER_CALL} per-call '
            f'limit. Splitting across calls is a later phase.'
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
        raise VlmReadError(f'Gemini call failed: {cause}') from cause

    if not response.text:
        raise VlmReadError('Gemini returned an empty response.')

    return _parse(response.text, len(crops))
