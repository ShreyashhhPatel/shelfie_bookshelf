import pytest

from scanner.services.vlm_read import (
    VLMReadError,
    _normalize_batch_results,
    extract_json_array,
    parse_read_response,
)

VALID_ARRAY = '[{"index": 1, "title": "Dune", "author": "Frank Herbert", "readable": true}]'


def test_parse_clean_json():
    assert parse_read_response(VALID_ARRAY) == [
        {"index": 1, "title": "Dune", "author": "Frank Herbert", "readable": True}
    ]


def test_parse_strips_markdown_code_fence():
    wrapped = f"```json\n{VALID_ARRAY}\n```"
    assert parse_read_response(wrapped) == parse_read_response(VALID_ARRAY)


def test_parse_strips_bare_code_fence():
    wrapped = f"```\n{VALID_ARRAY}\n```"
    assert parse_read_response(wrapped) == parse_read_response(VALID_ARRAY)


def test_parse_slices_leading_and_trailing_prose():
    # Malformed-JSON repair case: model wraps the array in commentary.
    wrapped = f"Sure, here is the JSON you asked for:\n{VALID_ARRAY}\nLet me know if you need anything else!"
    assert parse_read_response(wrapped) == parse_read_response(VALID_ARRAY)


def test_extract_json_array_finds_first_to_last_bracket():
    text = "noise before [1, 2, 3] noise after"
    assert extract_json_array(text) == "[1, 2, 3]"


def test_parse_raises_on_missing_brackets():
    with pytest.raises(VLMReadError):
        parse_read_response("this is not json at all")


def test_parse_raises_on_truncated_json():
    with pytest.raises(VLMReadError):
        parse_read_response('[{"index": 1, "title": "Dune", "author":')


def test_parse_raises_when_top_level_is_not_an_array():
    with pytest.raises(VLMReadError):
        parse_read_response('{"index": 1, "title": "Dune"}')


# ---------- duplicate crops of one physical book collapse to "duplicate" ----------


def _read(index, title, duplicate_of=None, readable=True):
    return {
        "index": index,
        "title": title,
        "author": "Agatha Christie",
        "readable": readable,
        "duplicate_of": duplicate_of,
    }


def test_duplicate_crop_is_marked_duplicate_and_keeps_its_title():
    results = _normalize_batch_results(
        [_read(1, "And Then There Were None"), _read(2, "And Then There Were None", duplicate_of=1)], 2
    )

    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "duplicate"
    assert results[1]["readable"] is False
    # Kept so the collapsed row can still show what it was a second crop of.
    assert results[1]["title"] == "And Then There Were None"


def test_self_reference_is_ignored():
    results = _normalize_batch_results([_read(1, "Dune", duplicate_of=1)], 1)
    assert results[0]["status"] == "ok"


def test_out_of_range_duplicate_pointer_is_ignored():
    results = _normalize_batch_results([_read(1, "Dune", duplicate_of=99)], 1)
    assert results[0]["status"] == "ok"


def test_boolean_duplicate_pointer_is_ignored():
    # bool is an int subclass, so True would otherwise resolve to index 1.
    results = _normalize_batch_results([_read(1, "Dune"), _read(2, "Dune", duplicate_of=True)], 2)
    assert results[1]["status"] == "ok"


def test_duplicate_chain_does_not_collapse_every_crop():
    # 3 -> 2 -> 1 must not leave the scan with nothing surviving.
    results = _normalize_batch_results(
        [_read(1, "Dune"), _read(2, "Dune", duplicate_of=1), _read(3, "Dune", duplicate_of=2)], 3
    )

    assert [r["status"] for r in results] == ["ok", "duplicate", "ok"]
    assert any(r["status"] == "ok" for r in results)


def test_unreadable_crop_is_never_reported_as_a_duplicate():
    results = _normalize_batch_results(
        [_read(1, "Dune"), _read(2, None, duplicate_of=1, readable=False)], 2
    )
    assert results[1]["status"] == "unreadable"
