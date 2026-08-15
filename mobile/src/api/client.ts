import { Platform } from "react-native";

import type { CatalogSearchResult, Detection, LibraryEntry, Scan } from "./types";

// The iOS Simulator shares the Mac's network namespace, so 127.0.0.1 reaches
// the local Django dev server directly. A physical device needs the Mac's
// LAN IP here instead -- set EXPO_PUBLIC_API_URL in mobile/.env.
const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch {
    throw new ApiError("Network error — check your connection and that the server is reachable.");
  }

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body?.error ?? JSON.stringify(body);
    } catch {
      // response body wasn't JSON -- fall through to the generic message
    }
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}

export type UploadImage = {
  uri: string;
  /** Present on web: the real File straight from the picker's <input>. */
  file?: Blob;
  name?: string;
};

export async function uploadScan(image: UploadImage): Promise<Scan> {
  const formData = new FormData();

  if (Platform.OS === "web") {
    // The browser's FormData only accepts real Blob/File values. The React
    // Native shape below would be stringified to "[object Object]", so the
    // server saw a text field and answered 400 "image file is required".
    let blob = image.file;
    if (!blob) {
      blob = await (await fetch(image.uri)).blob();
    }
    if (!blob) {
      throw new ApiError("Couldn't read that photo from the browser.");
    }
    formData.append("image", blob, image.name ?? "shelf.jpg");
  } else {
    // React Native's FormData accepts this shape even though it isn't a real Blob.
    formData.append("image", {
      uri: image.uri,
      name: "shelf.jpg",
      type: "image/jpeg",
    } as unknown as Blob);
  }

  return request<Scan>("/api/scans/", { method: "POST", body: formData });
}

export function getScan(id: number): Promise<Scan> {
  return request<Scan>(`/api/scans/${id}/`);
}

export function confirmDetection(
  id: number,
  payload: { book_id?: number; custom_title?: string; custom_author?: string }
): Promise<LibraryEntry> {
  const formData = new FormData();
  if (payload.book_id) formData.append("book_id", String(payload.book_id));
  if (payload.custom_title) formData.append("custom_title", payload.custom_title);
  if (payload.custom_author) formData.append("custom_author", payload.custom_author);
  return request<LibraryEntry>(`/api/detections/${id}/confirm/`, { method: "POST", body: formData });
}

export function discardDetection(id: number): Promise<Detection> {
  return request<Detection>(`/api/detections/${id}/discard/`, { method: "POST" });
}

export function retryDetection(id: number): Promise<Detection> {
  return request<Detection>(`/api/detections/${id}/retry/`, { method: "POST" });
}

export function searchCatalog(query: string): Promise<CatalogSearchResult[]> {
  return request<CatalogSearchResult[]>(`/api/catalog/search/?q=${encodeURIComponent(query)}`);
}

export function getLibrary(): Promise<LibraryEntry[]> {
  return request<LibraryEntry[]>("/api/library/");
}

export function deleteLibraryEntry(id: number): Promise<void> {
  return request<void>(`/api/library/${id}/`, { method: "DELETE" });
}
