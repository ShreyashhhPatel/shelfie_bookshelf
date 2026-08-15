/**
 * Mirrors of the Django serializer shapes.
 *
 * These are hand-written rather than generated, so they are a promise, not a
 * proof. If `scanner/serializers.py` changes, this file has to change with it
 * -- nothing will fail the build to remind you.
 *
 * The status unions below are the real contract between the two halves. They
 * mirror `TextChoices` on the Django models exactly, and every screen that
 * branches on state branches on these. Adding a state on the backend without
 * adding it here means the app silently renders an unhandled case.
 */

/** Mirrors `Scan.Status`. The lifecycle of one uploaded shelf photo. */
export type ScanStatus =
  | 'pending'
  | 'detecting'
  | 'reading'
  | 'matching'
  | 'complete'
  | 'failed';

/**
 * Mirrors `Detection.Status`. The lifecycle of one spine within a photo.
 *
 * `auto_matched` and `needs_review` are the fork the whole product hinges on:
 * a high-confidence match can be added directly, everything else goes in front
 * of the user. `confirmed` and `discarded` are that user's verdict.
 */
export type DetectionStatus =
  | 'pending'
  | 'auto_matched'
  | 'needs_review'
  | 'confirmed'
  | 'discarded';

/** Mirrors `LibraryBook.Source`. How a book got into the library. */
export type LibrarySource = 'scan' | 'manual';

/** Mirrors `CatalogBookSerializer`. Read-only reference data. */
export interface CatalogBook {
  id: number;
  title: string;
  author: string;
  year: number | null;
  /** Retitled editions. A US copy prints "The Golden Compass" on the spine. */
  alt_titles: string[];
  is_omnibus: boolean;
  /** Populated only when `is_omnibus`. Not a match key -- see AMBIGUITIES.md. */
  contained_titles: string[];
}

/**
 * Mirrors `LibraryBookSerializer`.
 *
 * `catalog_book` is null for a book the catalog does not have, which the
 * review step allows the user to keep anyway. In that case `title` and
 * `author` here are the only record of it, which is why they are always
 * present rather than read through the relation.
 */
export interface LibraryBook {
  id: number;
  title: string;
  author: string;
  source: LibrarySource;
  catalog_book: CatalogBook | null;
  added_at: string;
}

/** Mirrors DRF's `LimitOffsetPagination` envelope. */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/**
 * DRF's validation error body: field name to list of messages, plus the
 * `non_field_errors` bucket for anything raised by `validate()`.
 */
export type ValidationErrors = Record<string, string[] | undefined>;
