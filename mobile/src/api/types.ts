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

/** One ranked catalog possibility for a spine, as scored by the matcher. */
export interface MatchCandidate {
  catalog_book_id: number | null;
  title: string;
  author: string;
  /** Which of the entry's titles matched -- may be an alternate title. */
  matched_title: string;
  score: number;
  title_score: number;
  /** null when the spine carried no readable author at all. */
  author_score: number | null;
}

/** Mirrors `DetectionSerializer`. One spine within one photo. */
export interface Detection {
  id: number;
  /** [x1, y1, x2, y2] in source-image pixels, origin top-left. */
  bbox: [number, number, number, number] | number[];
  crop_url: string | null;
  /** The detector's confidence that this box is a spine, not that it read right. */
  confidence: number;
  raw_title: string;
  raw_author: string;
  candidates: MatchCandidate[];
  match: CatalogBook | null;
  /** Gap to the runner-up. This, not score, is what gated auto-accept. */
  margin: number;
  status: DetectionStatus;
}

export interface ScanCounts {
  total: number;
  auto_matched: number;
  needs_review: number;
}

/**
 * Mirrors `ReadErrorCode` on the backend. Why a scan failed.
 *
 * The split that matters is `is_retryable` on the Scan itself: a rate limit
 * clears on its own, a missing API key never does, and offering "try again"
 * for the second wastes the user's time.
 */
export type ScanErrorCode =
  | 'not_configured'
  | 'auth'
  | 'rate_limited'
  | 'timeout'
  | 'unavailable'
  | 'malformed_response'
  | 'unknown';

/** Mirrors `ScanSerializer`. */
export interface Scan {
  id: number;
  status: ScanStatus;
  /** A finished sentence, safe to render. Never a status code or payload. */
  error: string;
  error_code: ScanErrorCode | '';
  /** Server-computed: whether re-sending the same photo could plausibly work. */
  is_retryable: boolean;
  image_url: string | null;
  /** Milliseconds per pipeline stage, e.g. { detect: 1786, read: 5934 }. */
  timings: Record<string, number>;
  counts: ScanCounts;
  detections: Detection[];
  created_at: string;
}
