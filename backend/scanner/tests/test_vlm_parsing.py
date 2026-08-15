"""Everything the model can return that is not what it was asked for.

No network. `_parse` and its helpers are pure functions over a string, which
is the whole reason the parsing is separable from the call.

The rule these tests encode: a spine is never silently dropped. Whatever the
model does, every crop comes back with a read -- empty if necessary -- because
position is the only link between a read and the image it describes.
"""

import pytest

from scanner.services.vlm_read import (
    ReadErrorCode,
    VlmReadError,
    _parse,
    extract_json_array,
    resolve_duplicates,
)


class TestExtractJsonArray:
    def test_plain_array_passes_through(self):
        text = '[{"index": 0, "title": "Dune"}]'

        assert extract_json_array(text) == text

    def test_markdown_fence_is_stripped(self):
        """Models fence JSON even when asked not to, and even with
        response_mime_type set."""
        text = '```\n[{"index": 0, "title": "Dune"}]\n```'

        assert extract_json_array(text) == '[{"index": 0, "title": "Dune"}]'

    def test_json_tagged_fence_is_stripped(self):
        text = '```json\n[{"index": 0}]\n```'

        assert extract_json_array(text) == '[{"index": 0}]'

    def test_prose_on_either_side_is_sliced_away(self):
        text = 'Here are the spines I could read:\n[{"index": 0}]\nLet me know!'

        assert extract_json_array(text) == '[{"index": 0}]'

    def test_largest_fenced_block_wins(self):
        """A model that explains itself in a small fence and answers in a big
        one is more common than the reverse."""
        text = '```\nnote\n```\nand:\n```json\n[{"index": 0}, {"index": 1}]\n```'

        assert extract_json_array(text) == '[{"index": 0}, {"index": 1}]'

    def test_text_with_no_array_is_returned_unchanged(self):
        """So the caller raises a parse error rather than this silently
        inventing one."""
        assert extract_json_array('I cannot read these.') == 'I cannot read these.'


class TestParse:
    def test_well_formed_response(self):
        reads = _parse('[{"index": 0, "title": "Dune", "author": "Herbert"}]', 1)

        assert len(reads) == 1
        assert reads[0].title == 'Dune'
        assert reads[0].author == 'Herbert'

    def test_a_missing_index_becomes_an_empty_read(self):
        """The gap is filled rather than closed.

        Closing it would shift every later read onto the wrong crop, which is
        far worse than one spine coming back blank.
        """
        reads = _parse('[{"index": 0, "title": "Dune"}, {"index": 2, "title": "X"}]', 3)

        assert [r.index for r in reads] == [0, 1, 2]
        assert reads[1].is_empty
        assert reads[2].title == 'X'

    def test_out_of_range_index_is_dropped(self):
        reads = _parse('[{"index": 0, "title": "A"}, {"index": 99, "title": "B"}]', 1)

        assert len(reads) == 1
        assert reads[0].title == 'A'

    def test_non_dict_entries_are_skipped(self):
        reads = _parse('["garbage", 7, null, {"index": 0, "title": "A"}]', 1)

        assert reads[0].title == 'A'

    def test_non_integer_index_is_skipped(self):
        reads = _parse('[{"index": "zero", "title": "A"}]', 1)

        assert len(reads) == 1
        assert reads[0].is_empty

    def test_unparseable_body_raises_malformed(self):
        with pytest.raises(VlmReadError) as caught:
            _parse('not json at all', 1)

        assert caught.value.code is ReadErrorCode.MALFORMED_RESPONSE

    def test_a_json_object_instead_of_an_array_raises_malformed(self):
        with pytest.raises(VlmReadError) as caught:
            _parse('{"index": 0}', 1)

        assert caught.value.code is ReadErrorCode.MALFORMED_RESPONSE

    def test_fenced_response_parses_end_to_end(self):
        reads = _parse('```json\n[{"index": 0, "title": "Dune"}]\n```', 1)

        assert reads[0].title == 'Dune'

    def test_every_crop_gets_a_read_even_from_an_empty_array(self):
        reads = _parse('[]', 4)

        assert len(reads) == 4
        assert all(r.is_empty for r in reads)


class TestResolveDuplicates:
    """The pointer is model-supplied, so none of it is trusted."""

    def test_a_valid_backward_pointer_is_kept(self):
        assert resolve_duplicates({3: 2}, 5) == {3: 2}

    def test_self_reference_is_dropped(self):
        """Meaningless, and an infinite loop if followed naively."""
        assert resolve_duplicates({2: 2}, 5) == {}

    def test_forward_reference_is_dropped(self):
        """Backward-only is what makes cycles structurally impossible."""
        assert resolve_duplicates({1: 4}, 5) == {}

    def test_out_of_range_pointers_are_dropped(self):
        assert resolve_duplicates({1: -1}, 5) == {}
        assert resolve_duplicates({9: 1}, 5) == {}
        assert resolve_duplicates({2: 99}, 5) == {}

    def test_a_chain_is_flattened_to_its_root(self):
        """3 -> 2 -> 1 becomes 3 -> 1, so every duplicate names the original."""
        assert resolve_duplicates({2: 1, 3: 2}, 5) == {2: 1, 3: 1}

    def test_a_would_be_cycle_cannot_survive(self):
        """2 -> 3 and 3 -> 2: the forward half is dropped, breaking it."""
        assert resolve_duplicates({2: 3, 3: 2}, 5) == {3: 2}

    def test_one_bad_pointer_does_not_discard_the_good_ones(self):
        assert resolve_duplicates({1: 0, 2: 2, 3: 99, 4: 1}, 5) == {1: 0, 4: 0}

    def test_pointers_reach_parse(self):
        reads = _parse(
            '[{"index": 0, "title": "A"},'
            ' {"index": 1, "title": "A", "duplicate_of": 0}]',
            2,
        )

        assert reads[0].duplicate_of is None
        assert reads[1].duplicate_of == 0
        assert reads[1].is_duplicate

    def test_a_non_numeric_pointer_is_simply_not_a_duplicate_claim(self):
        reads = _parse('[{"index": 0, "title": "A", "duplicate_of": "yes"}]', 1)

        assert reads[0].duplicate_of is None
