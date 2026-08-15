export type DetectionStatus =
  | "auto_added"
  | "needs_review"
  | "unmatched"
  | "unreadable"
  | "failed"
  | "confirmed"
  | "discarded";

export type ScanStatus = "pending" | "processing" | "done" | "failed";

export interface Candidate {
  book_id: number;
  title: string;
  author: string;
  score: number;
}

export interface Detection {
  id: number;
  bbox: Record<string, number>;
  crop_url: string | null;
  read_title: string | null;
  read_author: string | null;
  status: DetectionStatus;
  confidence: number | null;
  margin: number | null;
  candidates: Candidate[];
  chosen_book: number | null;
}

export interface Scan {
  id: number;
  image_url: string | null;
  status: ScanStatus;
  timings: Record<string, number>;
  used_full_image_fallback: boolean;
  empty_result: boolean;
  created_at: string;
  detections: Detection[];
}

export interface LibraryEntry {
  id: number;
  book: number | null;
  title: string;
  author: string;
  created_at: string;
}

export interface CatalogSearchResult {
  id: number;
  title: string;
  author: string;
  year: number | null;
  series: string;
}
