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

/** Legacy session compatibility; no Book UI creates these references. */
export function selectedBooksToPayload(refs: SelectedBookReference[]): BookReferencePayload[] {
  return refs.map((ref) => ({ book_id: ref.bookId, page_ids: ref.pages.map((page) => page.pageId) }))
    .filter((ref) => ref.book_id && ref.page_ids.length);
}

export function normalizeBookReferences(value: unknown): BookReferencePayload[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is BookReferencePayload => !!item && typeof item === "object" && typeof (item as BookReferencePayload).book_id === "string" && Array.isArray((item as BookReferencePayload).page_ids));
}
