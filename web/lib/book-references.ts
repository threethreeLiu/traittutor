"use client";

export interface SelectedBookPage {
  bookId: string;
  bookTitle: string;
  pageId: string;
  pageTitle: string;
  chapterId?: string;
  chapterTitle?: string;
}

export interface SelectedBookReference {
  bookId: string;
  bookTitle: string;
  pages: SelectedBookPage[];
}

export interface BookReferencePayload {
  book_id: string;
  page_ids: string[];
}

export function selectedBooksToPayload(
  refs: SelectedBookReference[],
): BookReferencePayload[] {
  return refs
    .map((ref) => ({
      book_id: ref.bookId,
      page_ids: Array.from(
        new Set(ref.pages.map((page) => page.pageId)),
      ).filter(Boolean),
    }))
    .filter((ref) => ref.book_id && ref.page_ids.length > 0);
}

export function countSelectedBookPages(refs: SelectedBookReference[]): number {
  return refs.reduce((total, ref) => total + ref.pages.length, 0);
}

export function normalizeBookReferences(
  value: unknown,
): BookReferencePayload[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      const record: Record<string, unknown> =
        item && typeof item === "object"
          ? (item as Record<string, unknown>)
          : {};
      const bookId = typeof record.book_id === "string" ? record.book_id : "";
      const pageIds = Array.isArray(record.page_ids)
        ? record.page_ids.filter(
            (pageId): pageId is string =>
              typeof pageId === "string" && !!pageId,
          )
        : [];
      return bookId && pageIds.length
        ? { book_id: bookId, page_ids: Array.from(new Set(pageIds)) }
        : null;
    })
    .filter((item): item is BookReferencePayload => item !== null);
}
