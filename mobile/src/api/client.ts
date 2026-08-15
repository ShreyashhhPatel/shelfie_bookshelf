/**
 * The only place in the app that knows the API exists.
 *
 * Screens get typed functions and typed failures. They never see fetch, never
 * see a status code, and never have to remember that DRF returns 204 with an
 * empty body on delete.
 */

import type {
  CatalogBook,
  LibraryBook,
  Paginated,
  Scan,
  ValidationErrors,
} from './types';

/**
 * `EXPO_PUBLIC_` is the only prefix Expo inlines into the client bundle. The
 * fallback is the simulator's view of a local backend; a physical device on
 * the LAN needs the machine's IP instead, which is what .env.example explains.
 */
export const API_BASE_URL = (
  process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'
).replace(/\/$/, '');

const TIMEOUT_MS = 15000;

/**
 * A scan holds the request open for the entire pipeline -- detection, then a
 * hosted model read over every crop at once. Measured at roughly 8s for a
 * 24-spine shelf, so the normal timeout would abort a working scan.
 */
const SCAN_TIMEOUT_MS = 180000;

type RequestOptions = RequestInit & { timeoutMs?: number };

/**
 * Any failed request, network or HTTP.
 *
 * `status` is 0 when the request never reached the server -- the single most
 * common failure in development, and the one worth telling the user about
 * differently, since it usually means the backend is not running or the URL
 * points at localhost from a real phone.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly errors: ValidationErrors | null;

  constructor(message: string, status: number, errors: ValidationErrors | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.errors = errors;
  }

  /** True when the request never got a response at all. */
  get isNetworkError(): boolean {
    return this.status === 0;
  }

  /** First validation message for a field, if the server sent one. */
  fieldError(field: string): string | undefined {
    return this.errors?.[field]?.[0];
  }
}

function messageFromBody(body: unknown, status: number): string {
  if (typeof body === 'string' && body.trim()) return body;
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>;
    // DRF uses `detail` for permission and 404 style errors, and
    // `non_field_errors` for anything a serializer's validate() raised.
    if (typeof record.detail === 'string') return record.detail;
    const nonField = record.non_field_errors;
    if (Array.isArray(nonField) && typeof nonField[0] === 'string') return nonField[0];
    const first = Object.entries(record)[0];
    if (first && Array.isArray(first[1]) && typeof first[1][0] === 'string') {
      return `${first[0]}: ${first[1][0]}`;
    }
  }
  return `Request failed (${status})`;
}

function isValidationErrors(body: unknown): body is ValidationErrors {
  return (
    !!body &&
    typeof body === 'object' &&
    !Array.isArray(body) &&
    Object.values(body as Record<string, unknown>).every(
      (value) => value === undefined || Array.isArray(value),
    )
  );
}

async function request<T>(path: string, init: RequestOptions = {}): Promise<T> {
  const { timeoutMs = TIMEOUT_MS, ...fetchInit } = init;

  // AbortSignal.timeout() is not reliably present in the Hermes runtime, so
  // the controller is wired by hand.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...fetchInit,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        // FormData must set its own Content-Type: the multipart boundary is
        // generated at send time, and overriding it produces a body the
        // server cannot parse.
        ...(fetchInit.body && !(fetchInit.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...fetchInit.headers,
      },
    });
  } catch (cause) {
    const aborted = cause instanceof Error && cause.name === 'AbortError';
    throw new ApiError(
      aborted
        ? `The server did not respond within ${timeoutMs / 1000}s.`
        : `Could not reach the server at ${API_BASE_URL}.`,
      0,
    );
  } finally {
    clearTimeout(timer);
  }

  // 204 is the success case for DELETE and carries no body to parse.
  if (response.status === 204) {
    return undefined as T;
  }

  const raw = await response.text();
  let body: unknown = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = raw;
    }
  }

  if (!response.ok) {
    throw new ApiError(
      messageFromBody(body, response.status),
      response.status,
      isValidationErrors(body) ? body : null,
    );
  }

  return body as T;
}

export function getLibrary(): Promise<Paginated<LibraryBook>> {
  return request<Paginated<LibraryBook>>('/api/library/');
}

export function deleteLibraryEntry(id: number): Promise<void> {
  return request<void>(`/api/library/${id}/`, { method: 'DELETE' });
}

/**
 * Broad by design -- this backs a search box a human types into, so "dune"
 * returning the whole Herbert cluster is correct. It is not the spine matcher.
 */
export function searchCatalog(query: string): Promise<Paginated<CatalogBook>> {
  const trimmed = query.trim();
  if (!trimmed) {
    return Promise.resolve({ count: 0, next: null, previous: null, results: [] });
  }
  return request<Paginated<CatalogBook>>(
    `/api/catalog/search/?q=${encodeURIComponent(trimmed)}`,
  );
}

/**
 * Upload a shelf photo and get back the finished scan.
 *
 * The backend runs the whole pipeline inside this request, so it is slow by
 * design in this phase -- detection plus a hosted model read. The timeout is
 * raised accordingly rather than letting the default abort a working scan.
 */
export function uploadScan(uri: string): Promise<Scan> {
  const form = new FormData();
  // React Native's FormData takes this {uri, name, type} shape for files; it
  // is not the web File object and TypeScript's DOM lib does not describe it.
  form.append('image', {
    uri,
    name: 'shelf.jpg',
    type: 'image/jpeg',
  } as unknown as Blob);

  return request<Scan>('/api/scans/', {
    method: 'POST',
    body: form,
    timeoutMs: SCAN_TIMEOUT_MS,
  });
}

export function getScan(id: number): Promise<Scan> {
  return request<Scan>(`/api/scans/${id}/`);
}
