# catalog.csv — planted ambiguities

`catalog.csv` has 114 entries — 109 planted plus the five books in
`backend/test_photos`, so a scan of those shelves resolves against the catalog
instead of falling through to unmatched. Six ambiguities were deliberately planted per
`CONTEXT.md`. This file documents exactly where each one lives and how the
matcher (`scanner/services/matcher.py`) is expected to handle it.

## 1. Two editions of the same book as separate entries

`Fahrenheit 451` by Ray Bradbury appears twice — `year=1953` and `year=2013`.
Title and author are identical, so the matcher produces two candidates with
the same top score and ~0 margin. Result: **needs_review**, tie → user picks
the edition.

## 2. One book under two titles (UK/US)

- `Northern Lights` (Philip Pullman) has `alt_titles=The Golden Compass`.
- `Harry Potter and the Philosopher's Stone` (J.K. Rowling) has
  `alt_titles=Harry Potter and the Sorcerer's Stone`.

A VLM read of either title string should hit the *same* catalog row (alt-title
match), not create an ambiguity. This is a matching-correctness case, not a
review case.

## 3. Two genuinely different books sharing a title

- `The Power` — Naomi Alderman (2016, speculative fiction) vs. Rhonda Byrne
  (2010, self-help, part of "The Secret" series).
- `The Secret` — Rhonda Byrne (2006, self-help) vs. Julie Garwood (1992,
  historical romance).

Title score ties between the two candidates. If the VLM also read the author,
author score breaks the tie cleanly. If the author crop was unreadable, the
margin stays small → **needs_review**.

## 4. An omnibus alongside its individual volumes

- `The Lord of the Rings` (`is_omnibus=true`, `contained_titles` = Fellowship
  of the Ring | The Two Towers | The Return of the King) alongside three
  separate rows for each volume.
- `The Ultimate Hitchhiker's Guide to the Galaxy` (`is_omnibus=true`,
  contains all five Hitchhiker's Guide books) alongside separate rows for
  the first three individual volumes.

A spine reading "The Fellowship of the Ring" should match the individual
volume, not the omnibus — title score for the exact volume title beats the
omnibus's differently-worded title.

## 5. Titles that are substrings of others

- `Dune` vs. `Dune Messiah` (Frank Herbert).
- `Foundation` vs. `Foundation and Empire` vs. `Second Foundation` (Isaac
  Asimov).

A naive substring/contains match would conflate these. The fuzzy title score
must not give `Dune Messiah` a near-perfect score against a `Dune` read (and
vice versa) — this is exercised directly in
`scanner/tests/test_matcher.py::test_substring_trap_dune_vs_dune_messiah`.

## 6. Author names in multiple forms

- **Lastname, Firstname order:** `Animal Farm` — author stored as
  `Orwell, George`, while `1984` (same author) is stored as `George Orwell`.
- **Accents:** `One Hundred Years of Solitude` — author stored as
  `Gabriel García Márquez` (accented), while `Love in the Time of Cholera`
  (same author) is stored as `Gabriel Garcia Marquez` (unaccented).
- **Initials, spacing variant:** `Harry Potter and the Philosopher's Stone` —
  author `J.K. Rowling` (no spaces), while `The Casual Vacancy` (same author)
  is stored as `J. K. Rowling` (spaced initials).

The structured author matcher normalizes all three forms to the same surname
+ initials representation before comparing, so a VLM read of any form matches
a catalog row regardless of which form the catalog happened to store.
