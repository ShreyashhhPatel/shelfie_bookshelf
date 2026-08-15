# Planted ambiguities

`catalog.csv` is 109 rows. Most of them are ordinary and exist so the hard rows
have somewhere to hide. Six cases are planted deliberately, each one a distinct
way that "read the spine, look up the title" quietly returns the wrong book.

They are here because a matcher that is only ever tested against unambiguous
input will look finished and be wrong in production. Every rule below should
end up as a test in the phase that builds the matcher.

The recurring theme: **a high score is not the same as a confident answer.**
Cases 1, 4, 5, and 6 all produce a top candidate scoring near 1.0 that is a
coin flip between two rows. That is what `Detection.margin` exists for — the
gap to the runner-up, not the top score, is what may auto-accept a match.

---

## 1. Prefix collision — the Dune cluster

**Rows:** `Dune`, `Dune Messiah`, `Children of Dune`, `God Emperor of Dune`,
`Heretics of Dune` (all Frank Herbert). Second instance: `Foundation`,
`Foundation and Empire`, `Second Foundation` (Isaac Asimov).

**The failure:** substring search for `dune` returns five rows. A spine that
genuinely reads `DUNE` is the *shortest* of them, so any scorer that rewards
length of overlap, or that takes "first result", picks a sequel. The reverse
also bites: a spine reading `DUNE MESSIAH` partially matches `Dune`, and a
prefix-weighted scorer can rank the parent above the exact hit.

**Resolution:** exact match on `norm_title` wins outright and short-circuits
before any fuzzy pass. Fuzzy scoring must be symmetric — penalize catalog
tokens missing from the read as hard as it penalizes read tokens missing from
the catalog — so `Dune` does not score well against `Dune Messiah`.

**Note:** this cluster is also the search-endpoint smoke test. `?q=dune`
returns `Dune`, `Dune Messiah`, and `Children of Dune` among its hits; that is
correct behavior for *search*, which is meant to be broad. Search and matching
are different jobs and must not share a ranking function.

## 2. Alternate titles — Northern Lights / The Golden Compass

**Rows:** `Northern Lights` (Philip Pullman) carries `The Golden Compass` in
`alt_titles`. Five more: `The Hobbit` / *There and Back Again*, `1984` /
*Nineteen Eighty-Four*, `Slaughterhouse-Five` / *The Children's Crusade*,
`Gödel, Escher, Bach` / *GEB*, `The Design of Everyday Things` /
*The Psychology of Everyday Things* (a real 1990 retitling).

**The failure:** a US copy on the shelf physically prints `THE GOLDEN COMPASS`.
Matching only against `title` returns nothing, and the book falls through to
review — or worse, fuzzy-matches something unrelated rather than admitting the
miss.

**Resolution:** build the match index over `all_titles`, not `title`. An
alt-title hit is a full-strength match, not a discounted one, but the response
should report which string matched so the UI can show the user the name on the
spine they are holding.

**Also covers:** `1984` is the only numeric title in the catalog and the
likeliest thing a VLM garbles into a year, an ISBN fragment, or `l984`.

## 3. Omnibus containment — The Lord of the Rings

**Rows:** `The Lord of the Rings` (`is_omnibus=true`, contains the three
volumes), which are *also* present as three standalone rows. Same shape for
`His Dark Materials` over the Pullman trilogy and `The Earthsea Quartet` over
four Le Guin novels.

**The failure:** one spine, several works. A shelf holding the single-volume
LOTR and a shelf holding three separate paperbacks are different shelves, and
naive matching collapses them. Worse, `contained_titles` overlaps
`Northern Lights`, so the omnibus competes with case 2's alt-title row.

**Resolution:** `contained_titles` is *not* part of the match index. A spine
reading `THE TWO TOWERS` must match the standalone row, never the omnibus that
contains it; a spine reading `THE LORD OF THE RINGS` must match the omnibus,
never `The Fellowship of the Ring`. Containment is for what happens *after* a
match — offering "add all three volumes?" once the omnibus is confirmed.

## 4. Same title, different author — The Idiot

**Rows:** `The Idiot` / Fyodor Dostoevsky (1869) and `The Idiot` / Elif Batuman
(2017). The only duplicated title in the catalog.

**The failure:** title is not a key. Title-only matching returns two rows with
identical scores, and picking either one is a 50% error rate. This is why the
uniqueness constraint in `models.py` is on `(norm_title, norm_author)`.

**Resolution:** author is the tiebreak. If the VLM read an author, match on the
pair. If it did not — very common, since many spines print only the title, or
the author is in unreadable type at the foot — the correct outcome is *not* to
guess: margin is zero, and the detection goes to review with both rows offered
as candidates.

## 5. Same author name, different people — David Mitchell

**Rows:** `Cloud Atlas`, `The Bone Clocks`, `number9dream` (David Mitchell, the
novelist) and `Back Story` (David Mitchell, the comedian — a real and
separate person).

**The failure:** the inverse of case 4. Author is not a key either, so author
is only a *tiebreak* and never a filter you trust on its own. A matcher that
resolves a poorly-read title by leaning on a confidently-read author will
happily put a memoir by one man into a library of novels by another.

**Resolution:** never let author alone select a row. Author raises or lowers a
candidate that a title already put in contention. A spine where only the author
is legible must go to review — `author_surname()` in `constants.py` is the
coarsest fallback key for a reason, and its docstring says so.

**Near miss, not planted:** `Charlotte Brontë` and `Emily Brontë` share a
surname and a publication year (1847). Useful for testing that
`author_surname()` is treated as weak evidence.

## 6. Same main title, different subtitle — Sapiens

**Rows:** `Sapiens: A Brief History of Humankind` (2011) and
`Sapiens: A Graphic History, Volume 1` (2020), both Yuval Noah Harari. Adjacent
rows: `Homo Deus: A Brief History of Tomorrow` shares the *subtitle* pattern
with the first, and `The Gene: An Intimate History` /
`The Emperor of All Maladies: A Biography of Cancer` are the same author with
different subtitles.

**The failure:** subtitle stripping is the obvious normalization, and it is
wrong here. Reduce both rows to `sapiens` and they become the same book, which
also violates the `(norm_title, norm_author)` constraint at load time. The two
are genuinely different objects and look completely different on a shelf.

**Resolution:** `normalize_title()` deliberately keeps subtitles.
`main_title()` exists as a separate, explicitly lossy helper for *widening a
search after an exact match has already failed* — never for deciding one. A
spine reading only `SAPIENS` (very likely: the subtitle is set small and is the
first thing a crop loses) matches both at equal score, margin is zero, and it
goes to review. That is the right answer, not a bug.

---

## Summary

| # | Case | Canonical rows | Resolved by |
|---|------|----------------|-------------|
| 1 | Prefix collision | Dune ×5, Foundation ×3 | Exact match short-circuits; symmetric fuzzy scoring |
| 2 | Alternate titles | Northern Lights (+5 more) | Index over `all_titles`; full-strength hit |
| 3 | Omnibus containment | LOTR, His Dark Materials, Earthsea Quartet | `contained_titles` excluded from the index |
| 4 | Same title, two authors | The Idiot ×2 | Author as tiebreak; review when absent |
| 5 | Same author, two people | David Mitchell ×4 | Author never selects alone |
| 6 | Same title, two subtitles | Sapiens ×2 | Subtitles kept in `normalize_title()` |
